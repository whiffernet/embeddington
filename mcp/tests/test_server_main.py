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


@pytest.fixture
def fake_asyncio_run(monkeypatch):
    """Stub srv.asyncio.run to close the coroutine instead of running it.

    Lets main() proceed past `asyncio.run(_isolation_sanity_check())` without
    an event loop, and without the "coroutine was never awaited" warning.
    """

    def _fake(coro):
        coro.close()

    monkeypatch.setattr(srv.asyncio, "run", _fake)


@pytest.fixture
def fake_mcp_run(monkeypatch):
    """Stub srv.mcp.run so main() returns instead of blocking on stdio."""
    monkeypatch.setattr(srv.mcp, "run", lambda: None)


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


# --- Remote-root refusal ----------------------------------------------------


def test_main_refuses_remote_root(monkeypatch):
    monkeypatch.setattr(srv.config, "ARANGO_PASSWORD", "pw")
    monkeypatch.setattr(srv.config, "ARANGO_USER", "root")
    monkeypatch.setattr(srv.config, "ARANGO_URL", "http://spark-a4ad.local:8529")
    monkeypatch.delenv("EMBEDDINGTON_ALLOW_REMOTE_ROOT", raising=False)
    with pytest.raises(SystemExit, match="kg_servicenow_ro"):
        srv.main()


def test_main_allows_remote_root_with_explicit_override(
    monkeypatch, fake_asyncio_run, fake_mcp_run
):
    monkeypatch.setattr(srv.config, "ARANGO_PASSWORD", "pw")
    monkeypatch.setattr(srv.config, "ARANGO_USER", "root")
    monkeypatch.setattr(srv.config, "ARANGO_URL", "http://spark-a4ad.local:8529")
    monkeypatch.setenv("EMBEDDINGTON_ALLOW_REMOTE_ROOT", "1")
    srv.main()  # proceeds to the (stubbed) run stage


def test_main_allows_local_root(monkeypatch, fake_asyncio_run, fake_mcp_run):
    monkeypatch.setattr(srv.config, "ARANGO_PASSWORD", "pw")
    monkeypatch.setattr(srv.config, "ARANGO_USER", "root")
    monkeypatch.setattr(srv.config, "ARANGO_URL", "http://localhost:8529")
    srv.main()


@pytest.mark.asyncio
async def test_isolation_check_sets_lexical_status_via_chunk_text_status(monkeypatch):
    """_isolation_sanity_check must probe the lexical chunk_text status on
    every start and record the result in _lexical_status (spec §5 PR 4,
    issue #38) — not just the pre-existing Qdrant reachability check. The
    probe is read-only: startup never ensures/writes the index itself."""
    monkeypatch.setattr(srv, "_lexical_status", "absent")  # revert to this after the test

    fake_qdrant = AsyncMock()
    fake_qdrant.can_read_collection = AsyncMock(return_value=True)
    fake_qdrant.chunk_text_status = AsyncMock(return_value="ready")
    monkeypatch.setattr(srv, "_get_qdrant", lambda collection=None: fake_qdrant)

    await srv._isolation_sanity_check()

    fake_qdrant.chunk_text_status.assert_awaited_once()
    assert srv._lexical_status == "ready"


@pytest.mark.asyncio
async def test_isolation_check_survives_chunk_text_status_failure(monkeypatch):
    """A failed chunk_text status probe must degrade to 'unavailable', never
    abort startup — the lexical lane is an enhancement, not a hard
    dependency."""
    monkeypatch.setattr(srv, "_lexical_status", "absent")  # revert to this after the test

    fake_qdrant = AsyncMock()
    fake_qdrant.can_read_collection = AsyncMock(return_value=True)
    fake_qdrant.chunk_text_status = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(srv, "_get_qdrant", lambda collection=None: fake_qdrant)

    await srv._isolation_sanity_check()  # must not raise

    assert srv._lexical_status == "unavailable"


# --- Arango / embed startup probes (warn-only) ------------------------------


@pytest.mark.asyncio
async def test_isolation_check_warns_on_arango_probe_failure(monkeypatch, caplog):
    """A failing Arango probe (wrong ARANGO_DATABASE, missing grant, etc.)
    must log a warning naming db+user and let startup complete — never
    raise. This is what turns a misconfigured BYO-prod store into a loud
    boot-time signal instead of silent empty KG results."""
    fake_qdrant = AsyncMock()
    fake_qdrant.can_read_collection = AsyncMock(return_value=True)
    fake_qdrant.chunk_text_status = AsyncMock(return_value="ready")
    monkeypatch.setattr(srv, "_get_qdrant", lambda collection=None: fake_qdrant)

    fake_arango = MagicMock()
    fake_arango.probe_read = MagicMock(side_effect=RuntimeError("no grant"))
    monkeypatch.setattr(srv, "_get_arango", lambda: fake_arango)

    fake_embed = AsyncMock()
    fake_embed.embed = AsyncMock(return_value=[0.0] * 1024)
    monkeypatch.setattr(srv, "_get_embed", lambda *a, **k: fake_embed)

    with caplog.at_level("WARNING"):
        await srv._isolation_sanity_check()  # must not raise

    assert any("Arango probe FAILED" in r.message for r in caplog.records)
    assert any(
        srv.config.ARANGO_DATABASE in r.message for r in caplog.records if "Arango" in r.message
    )


@pytest.mark.asyncio
async def test_isolation_check_logs_arango_probe_pass(monkeypatch, caplog):
    """Happy path: a working Arango probe logs a pass, not a warning."""
    fake_qdrant = AsyncMock()
    fake_qdrant.can_read_collection = AsyncMock(return_value=True)
    fake_qdrant.chunk_text_status = AsyncMock(return_value="ready")
    monkeypatch.setattr(srv, "_get_qdrant", lambda collection=None: fake_qdrant)

    fake_arango = MagicMock()
    fake_arango.probe_read = MagicMock(return_value=None)
    monkeypatch.setattr(srv, "_get_arango", lambda: fake_arango)

    fake_embed = AsyncMock()
    fake_embed.embed = AsyncMock(return_value=[0.0] * 1024)
    monkeypatch.setattr(srv, "_get_embed", lambda *a, **k: fake_embed)

    with caplog.at_level("INFO"):
        await srv._isolation_sanity_check()

    assert any("Arango probe passed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_isolation_check_warns_on_embed_probe_failure(monkeypatch, caplog):
    """A failing embed probe (unreachable EMBED_URL or wrong dims — the
    client raises on both, no separate dim branch) must log a warning and
    let startup complete — never raise."""
    fake_qdrant = AsyncMock()
    fake_qdrant.can_read_collection = AsyncMock(return_value=True)
    fake_qdrant.chunk_text_status = AsyncMock(return_value="ready")
    monkeypatch.setattr(srv, "_get_qdrant", lambda collection=None: fake_qdrant)

    fake_arango = MagicMock()
    fake_arango.probe_read = MagicMock(return_value=None)
    monkeypatch.setattr(srv, "_get_arango", lambda: fake_arango)

    fake_embed = AsyncMock()
    fake_embed.embed = AsyncMock(side_effect=RuntimeError("unreachable"))
    monkeypatch.setattr(srv, "_get_embed", lambda *a, **k: fake_embed)

    with caplog.at_level("WARNING"):
        await srv._isolation_sanity_check()  # must not raise

    assert any("Embed probe FAILED" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_isolation_check_logs_embed_probe_pass(monkeypatch, caplog):
    """Happy path: a working embed probe logs a pass, not a warning."""
    fake_qdrant = AsyncMock()
    fake_qdrant.can_read_collection = AsyncMock(return_value=True)
    fake_qdrant.chunk_text_status = AsyncMock(return_value="ready")
    monkeypatch.setattr(srv, "_get_qdrant", lambda collection=None: fake_qdrant)

    fake_arango = MagicMock()
    fake_arango.probe_read = MagicMock(return_value=None)
    monkeypatch.setattr(srv, "_get_arango", lambda: fake_arango)

    fake_embed = AsyncMock()
    fake_embed.embed = AsyncMock(return_value=[0.0] * 1024)
    monkeypatch.setattr(srv, "_get_embed", lambda *a, **k: fake_embed)

    with caplog.at_level("INFO"):
        await srv._isolation_sanity_check()

    assert any("Embed probe passed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_maybe_reprobe_throttles_to_once_per_60s(monkeypatch):
    """_maybe_reprobe must not hammer Qdrant on every degraded tool call —
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
    fake_qdrant.chunk_text_status = AsyncMock(return_value="building")
    monkeypatch.setattr(srv, "_get_qdrant", lambda collection=None: fake_qdrant)

    await srv._maybe_reprobe()
    await srv._maybe_reprobe()  # back-to-back call within the window: no-op

    assert fake_qdrant.chunk_text_status.await_count == 1

    # Advance the guard's clock past the interval -> the next call fires again.
    monkeypatch.setattr(
        srv, "_lexical_last_reensure", time.monotonic() - srv._LEXICAL_REENSURE_INTERVAL - 1
    )
    await srv._maybe_reprobe()

    assert fake_qdrant.chunk_text_status.await_count == 2


@pytest.mark.asyncio
async def test_maybe_reprobe_guards_against_concurrent_duplicate_probe(monkeypatch):
    """A read-only status probe is sub-second, so overlap is no longer the
    correctness hazard it was for the old multi-minute materialize — but the
    in-flight flag is kept for call-coalescing symmetry: two tool calls
    racing in before the first probe returns must share one probe rather
    than each firing their own."""
    monkeypatch.setattr(srv, "_lexical_status", "absent")
    monkeypatch.setattr(
        srv, "_lexical_last_reensure", time.monotonic() - srv._LEXICAL_REENSURE_INTERVAL - 1
    )
    monkeypatch.setattr(srv, "_lexical_reensure_in_flight", False)

    release = asyncio.Event()
    calls = {"n": 0}

    async def slow_status():
        calls["n"] += 1
        await release.wait()
        return "ready"

    fake_qdrant = AsyncMock()
    fake_qdrant.chunk_text_status = slow_status
    monkeypatch.setattr(srv, "_get_qdrant", lambda collection=None: fake_qdrant)

    t1 = asyncio.create_task(srv._maybe_reprobe())
    t2 = asyncio.create_task(srv._maybe_reprobe())
    await asyncio.sleep(0)  # let t1 start (and set the in-flight flag) before t2 races in

    assert calls["n"] == 1  # t2 saw the flag and no-opped before ever calling chunk_text_status

    release.set()
    await asyncio.gather(t1, t2)

    assert calls["n"] == 1
    assert srv._lexical_reensure_in_flight is False  # cleared after completion


# --- Zero-write contract ----------------------------------------------------
#
# The repo-wide invariant: no code path in mcp/ may ever issue a Qdrant
# mutation (POST /points/payload, PUT /index, or any PUT/DELETE at all).
# This file stubs at the CLIENT-method layer everywhere else (_get_qdrant ->
# AsyncMock), which can't observe HTTP verbs. Here we build a REAL
# QdrantSearchClient and monkeypatch only its `_http` to a recording fake, so
# every request the client would actually send is visible.


def _async_return(x):
    """Wrap a value as a zero-arg async callable returning it.

    Used to monkeypatch ``QdrantSearchClient._http`` (normally an async
    method) with something that resolves to a fixed fake client instance.

    Args:
        x: The value the returned coroutine function will resolve to.

    Returns:
        An ``async def`` callable taking no arguments that returns ``x``.
    """

    async def _inner():
        return x

    return _inner


class _Resp:
    """Minimal stand-in for an httpx.Response used by RecordingHttp."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class RecordingHttp:
    """Fake httpx client recording (method, url); serves reads, fails writes."""

    def __init__(self):
        self.calls = []

    async def get(self, url, **kw):
        self.calls.append(("GET", url))
        return _Resp(200, {"result": {"status": "green", "payload_schema": {}}})

    async def post(self, url, json=None, **kw):
        self.calls.append(("POST", url))
        if url.endswith("/points/search"):
            return _Resp(200, {"result": []})
        if url.endswith("/points/scroll"):
            return _Resp(200, {"result": {"points": [], "next_page_offset": None}})
        raise AssertionError(f"unexpected POST (write?): {url}")

    async def put(self, url, **kw):
        # Record before raising: _isolation_sanity_check and _maybe_reprobe
        # both wrap their ensure/probe call in a broad `except Exception` (by
        # design — a failed lexical-lane check must never block startup or
        # fail a tool call), which would otherwise swallow this
        # AssertionError before it ever reached the test. Recording the call
        # first means the write attempt is still caught by the `_writes()`
        # assertion below even when the caller degrades gracefully instead
        # of propagating.
        self.calls.append(("PUT", url))
        raise AssertionError(f"WRITE detected: PUT {url}")


def _writes(calls):
    return [
        (m, u)
        for m, u in calls
        if "/points/payload" in u or u.endswith("/index") or m in ("PUT", "DELETE")
    ]


@pytest.mark.asyncio
async def test_mcp_is_write_free_across_startup_and_tools(monkeypatch):
    """No code path in mcp/ (startup + both Qdrant-touching tools) may write.

    Builds a real QdrantSearchClient (not a client-layer mock) so the actual
    HTTP verbs it issues are observable, and drives it through startup plus
    both tools that touch Qdrant. Any PUT, DELETE, or POST to a mutating
    endpoint fails the fake outright; the assertion is a belt-and-suspenders
    scan of everything recorded.
    """
    rec = RecordingHttp()
    real = srv.QdrantSearchClient(url="http://q", collection="technology")
    monkeypatch.setattr(real, "_http", _async_return(rec))
    monkeypatch.setattr(srv, "_get_qdrant", lambda *a, **k: real)
    fake_embed = AsyncMock()
    fake_embed.embed.return_value = [0.0] * 1024
    monkeypatch.setattr(srv, "_get_embed", lambda *a, **k: fake_embed)
    monkeypatch.setattr(srv, "_get_arango", lambda *a, **k: MagicMock())

    await srv._isolation_sanity_check()
    await srv.vector_search(query="q")
    await srv.enrich(query="q")

    assert _writes(rec.calls) == []
