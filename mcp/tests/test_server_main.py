"""Tests for the server.main() lifecycle.

Specifically: after the synchronous sanity check (which runs inside its own
``asyncio.run()`` loop) completes, the lazy client registries must be reset
to empty dicts so the first MCP request rebuilds clients on FastMCP's running
loop. Without this reset, the first request hits "Event loop is closed" from
httpx.AsyncClient instances bound to the now-closed sanity-check loop.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
import server as srv


def test_main_resets_singletons_after_sanity_check(monkeypatch):
    # Pretend the sanity check ran and populated the client registries.
    monkeypatch.setattr(srv, "_embed_clients", {"technology": MagicMock()})
    monkeypatch.setattr(srv, "_qdrant_clients", {"technology": MagicMock()})

    # Make ARANGO_PASSWORD truthy so main() doesn't bail early.
    monkeypatch.setattr(srv.config, "ARANGO_PASSWORD", "test-pw")

    def fake_asyncio_run(coro):
        coro.close()  # avoid "coroutine was never awaited" warning

    monkeypatch.setattr(srv.asyncio, "run", fake_asyncio_run)

    ran = {"mcp_run_called": False}

    def fake_mcp_run():
        ran["mcp_run_called"] = True

    monkeypatch.setattr(srv.mcp, "run", fake_mcp_run)

    srv.main()

    assert ran["mcp_run_called"] is True
    assert srv._embed_clients == {}, "expected embed registry reset after sanity check"
    assert srv._qdrant_clients == {}, "expected qdrant registry reset after sanity check"


def test_main_aborts_when_arango_password_missing(monkeypatch):
    monkeypatch.setattr(srv.config, "ARANGO_PASSWORD", "")

    with pytest.raises(SystemExit, match="ARANGO_PASSWORD"):
        srv.main()


@pytest.mark.asyncio
async def test_isolation_check_sets_lexical_status_via_ensure_chunk_text(monkeypatch):
    """_isolation_sanity_check must probe + ensure the lexical chunk_text
    index on every start and record the result in _lexical_status (spec §5
    PR 4, issue #38) — not just the pre-existing Qdrant reachability check."""
    monkeypatch.setattr(srv, "_lexical_status", "absent")  # revert to this after the test

    fake_qdrant = AsyncMock()
    fake_qdrant.can_read_collection = AsyncMock(return_value=True)
    fake_qdrant.ensure_chunk_text = AsyncMock(return_value="ready")
    monkeypatch.setattr(srv, "_get_qdrant", lambda collection=None: fake_qdrant)

    await srv._isolation_sanity_check()

    fake_qdrant.ensure_chunk_text.assert_awaited_once()
    assert srv._lexical_status == "ready"


@pytest.mark.asyncio
async def test_isolation_check_survives_ensure_chunk_text_failure(monkeypatch):
    """A failed chunk_text ensure must degrade to 'unavailable', never abort
    startup — the lexical lane is an enhancement, not a hard dependency."""
    monkeypatch.setattr(srv, "_lexical_status", "absent")  # revert to this after the test

    fake_qdrant = AsyncMock()
    fake_qdrant.can_read_collection = AsyncMock(return_value=True)
    fake_qdrant.ensure_chunk_text = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(srv, "_get_qdrant", lambda collection=None: fake_qdrant)

    await srv._isolation_sanity_check()  # must not raise

    assert srv._lexical_status == "unavailable"


@pytest.mark.asyncio
async def test_maybe_reensure_throttles_to_once_per_60s(monkeypatch):
    """_maybe_reensure must not hammer Qdrant on every degraded tool call —
    at most once per process per 60s via the module-global monotonic guard
    (spec §5 PR 4, issue #38)."""
    monkeypatch.setattr(srv, "_lexical_status", "absent")
    # NOT 0.0: time.monotonic()'s reference point is unspecified (often system
    # boot, not epoch) — in a short-uptime environment (e.g. a freshly booted
    # CI runner) 0.0 can be LESS than 60s behind "now", which makes the guard
    # incorrectly treat this as still-within-window and skip the first call
    # too (reproduced locally by faking a low-uptime monotonic clock). A
    # relative offset from the real "now" is the only value guaranteed to be
    # more than the interval in the past regardless of the clock's epoch.
    monkeypatch.setattr(
        srv, "_lexical_last_reensure", time.monotonic() - srv._LEXICAL_REENSURE_INTERVAL - 1
    )

    fake_qdrant = AsyncMock()
    fake_qdrant.ensure_chunk_text = AsyncMock(return_value="building")
    monkeypatch.setattr(srv, "_get_qdrant", lambda collection=None: fake_qdrant)

    await srv._maybe_reensure()
    await srv._maybe_reensure()  # back-to-back call within the window: no-op

    assert fake_qdrant.ensure_chunk_text.await_count == 1

    # Advance the guard's clock past the interval -> the next call fires again.
    monkeypatch.setattr(
        srv, "_lexical_last_reensure", time.monotonic() - srv._LEXICAL_REENSURE_INTERVAL - 1
    )
    await srv._maybe_reensure()

    assert fake_qdrant.ensure_chunk_text.await_count == 2


@pytest.mark.asyncio
async def test_maybe_reensure_guards_against_concurrent_duplicate_materialize(monkeypatch):
    """A full-corpus materialize (~3m30s, measured) outlives the 60s throttle
    window, so a second tool call arriving mid-materialize would otherwise
    see the window as elapsed and fire a second, concurrent
    ensure_chunk_text() — double-scrolling the whole collection. The
    in-flight flag must stop the second call from ever reaching Qdrant."""
    monkeypatch.setattr(srv, "_lexical_status", "absent")
    monkeypatch.setattr(
        srv, "_lexical_last_reensure", time.monotonic() - srv._LEXICAL_REENSURE_INTERVAL - 1
    )
    monkeypatch.setattr(srv, "_lexical_reensure_in_flight", False)

    release = asyncio.Event()
    calls = {"n": 0}

    async def slow_ensure():
        calls["n"] += 1
        await release.wait()
        return "ready"

    fake_qdrant = AsyncMock()
    fake_qdrant.ensure_chunk_text = slow_ensure
    monkeypatch.setattr(srv, "_get_qdrant", lambda collection=None: fake_qdrant)

    t1 = asyncio.create_task(srv._maybe_reensure())
    t2 = asyncio.create_task(srv._maybe_reensure())
    await asyncio.sleep(0)  # let t1 start (and set the in-flight flag) before t2 races in

    assert calls["n"] == 1  # t2 saw the flag and no-opped before ever calling ensure_chunk_text

    release.set()
    await asyncio.gather(t1, t2)

    assert calls["n"] == 1
    assert srv._lexical_reensure_in_flight is False  # cleared after completion
