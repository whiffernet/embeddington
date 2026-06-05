"""Tests for the server.main() lifecycle.

Specifically: after the synchronous sanity check (which runs inside its own
``asyncio.run()`` loop) completes, the lazy client registries must be reset
to empty dicts so the first MCP request rebuilds clients on FastMCP's running
loop. Without this reset, the first request hits "Event loop is closed" from
httpx.AsyncClient instances bound to the now-closed sanity-check loop.
"""

from unittest.mock import MagicMock

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
    assert srv._qdrant_clients == {}, (
        "expected qdrant registry reset after sanity check"
    )


def test_main_aborts_when_arango_password_missing(monkeypatch):
    monkeypatch.setattr(srv.config, "ARANGO_PASSWORD", "")

    import pytest

    with pytest.raises(SystemExit, match="ARANGO_PASSWORD"):
        srv.main()
