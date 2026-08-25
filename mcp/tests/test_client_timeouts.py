"""Every backend client gets a configured timeout.

The MCP server is built to DEGRADE: enrich catches a backend's error, routes it into
`errors`, and returns the other half with a grounding tier saying what is missing. That
only works when a failure raises. A hang raises nothing, so an unbounded client turns a
degraded answer into silence — and `enrich` makes several SERIAL Arango calls, so the wait
multiplies.

Arango was the one client without a timeout: it inherited python-arango's 60s default while
Qdrant and embed both took config.HTTP_TIMEOUT. This file pins all three so the next client
added cannot quietly be the fourth.
"""

import config
import pytest
import server as srv
from arango_client import ArangoKGClient


def test_arango_client_passes_its_timeout_to_the_driver(monkeypatch):
    captured = {}

    class FakeArangoClient:
        def __init__(self, hosts, **kwargs):
            captured["hosts"] = hosts
            captured.update(kwargs)

        def db(self, *a, **k):
            return object()

    monkeypatch.setattr("arango_client.ArangoClient", FakeArangoClient)
    ArangoKGClient(url="http://x:8529", database="d", username="u", password="p", timeout=7.5)
    assert captured["request_timeout"] == 7.5, (
        "an unbounded client turns a wedged Arango into a hung tool call"
    )


def test_arango_client_has_a_bounded_default(monkeypatch):
    """Even constructed without an explicit timeout it must not inherit 60s."""
    captured = {}

    class FakeArangoClient:
        def __init__(self, hosts, **kwargs):
            captured.update(kwargs)

        def db(self, *a, **k):
            return object()

    monkeypatch.setattr("arango_client.ArangoClient", FakeArangoClient)
    ArangoKGClient(url="http://x:8529", database="d", username="u", password="p")
    assert captured.get("request_timeout") is not None
    assert captured["request_timeout"] <= 30.0


@pytest.mark.parametrize(
    "getter,cls_name,module",
    [
        ("_get_arango", "ArangoKGClient", "server"),
        ("_get_qdrant", "QdrantSearchClient", "server"),
        ("_get_embed", "EmbeddingClient", "server"),
    ],
)
def test_every_backend_client_is_built_with_the_configured_timeout(
    monkeypatch, getter, cls_name, module
):
    """The wiring, not just the constructor: a client that accepts a timeout and is never
    given one is the same bug wearing a different hat."""
    captured = {}

    class Fake:
        def __init__(self, *a, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(f"{module}.{cls_name}", Fake)
    # Clear whichever singleton this getter memoises.
    for name in ("_arango", "_qdrant_clients", "_embed_clients"):
        if hasattr(srv, name):
            monkeypatch.setattr(srv, name, None if name == "_arango" else {})

    getattr(srv, getter)()
    assert captured.get("timeout") == config.HTTP_TIMEOUT, (
        f"{cls_name} was built without the configured timeout"
    )
