"""Tests for consumer.lexical_index -- the sync warm-up twin of mcp/qdrant_client.py's
async chunk_text ensure logic. Uses httpx.MockTransport, matching the async suite's
style in mcp/tests/test_qdrant_client.py."""

import json

import httpx
import pytest

from consumer import lexical_index


def _collection_info(payload_schema: dict, status: str = "green") -> dict:
    return {"result": {"status": status, "payload_schema": payload_schema}}


def _mock(monkeypatch, handler):
    """Point every lexical_index HTTP call at a MockTransport, like the MCP suite."""
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        lexical_index, "_client", lambda timeout: httpx.Client(transport=transport, timeout=timeout)
    )


# --- chunk_text_status -----------------------------------------------------


def test_status_ready_building_absent(monkeypatch):
    state = {"schema": {}, "status": "green"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/collections/technology"
        return httpx.Response(200, json=_collection_info(state["schema"], state["status"]))

    _mock(monkeypatch, handler)

    assert lexical_index.chunk_text_status("http://q", "technology") == "absent"
    state["schema"] = {"chunk_text": {"data_type": "text"}}
    state["status"] = "yellow"
    assert lexical_index.chunk_text_status("http://q", "technology") == "building"
    state["status"] = "green"
    assert lexical_index.chunk_text_status("http://q", "technology") == "ready"


def test_status_unavailable_on_non_200(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _mock(monkeypatch, handler)

    assert lexical_index.chunk_text_status("http://q", "technology") == "unavailable"


def test_status_unavailable_on_transport_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _mock(monkeypatch, handler)

    assert lexical_index.chunk_text_status("http://q", "technology") == "unavailable"


# --- materialize_chunk_text -------------------------------------------------


def test_materialize_writes_batches_skips_no_prose_and_follows_cursor(monkeypatch):
    calls = {"scroll": 0, "payload": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/points/scroll"):
            body = json.loads(request.content)
            calls["scroll"] += 1
            if calls["scroll"] == 1:
                assert "offset" not in body  # first page: no cursor yet
                return httpx.Response(
                    200,
                    json={
                        "result": {
                            "points": [
                                {
                                    "id": 1,
                                    "payload": {"_node_content": json.dumps({"text": "prose one"})},
                                },
                                {"id": 2, "payload": {"_node_content": json.dumps({"text": ""})}},
                                {"id": 3, "payload": {"text": "direct"}},
                            ],
                            "next_page_offset": "cursor-2",
                        }
                    },
                )
            assert body["offset"] == "cursor-2"  # second page: follows the cursor
            return httpx.Response(
                200,
                json={
                    "result": {
                        "points": [{"id": 4, "payload": {"text": "prose four"}}],
                        "next_page_offset": None,
                    }
                },
            )
        if request.url.path.endswith("/points/payload"):
            calls["payload"].append(json.loads(request.content))
            return httpx.Response(200, json={"result": {}, "status": "ok"})
        raise AssertionError(request.url.path)

    _mock(monkeypatch, handler)

    n = lexical_index.materialize_chunk_text("http://q", "technology")

    assert n == 3  # point 2 had no recoverable prose -> skipped
    assert calls["scroll"] == 2  # followed next_page_offset to the second page, then stopped
    by_ids = {
        tuple(sorted(call["points"])): call["payload"]["chunk_text"] for call in calls["payload"]
    }
    assert by_ids == {(1,): "prose one", (3,): "direct", (4,): "prose four"}


def test_materialize_groups_distinct_text_within_a_page(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/points/scroll"):
            return httpx.Response(
                200,
                json={
                    "result": {
                        "points": [
                            {"id": 10, "payload": {"text": "shared"}},
                            {"id": 11, "payload": {"text": "shared"}},
                        ],
                        "next_page_offset": None,
                    }
                },
            )
        if request.url.path.endswith("/points/payload"):
            return httpx.Response(200, json={"result": {}, "status": "ok"})
        raise AssertionError(request.url.path)

    calls = []

    def spy_handler(request):
        resp = handler(request)
        if request.url.path.endswith("/points/payload"):
            calls.append(json.loads(request.content))
        return resp

    _mock(monkeypatch, spy_handler)

    n = lexical_index.materialize_chunk_text("http://q", "technology")

    assert n == 2
    assert len(calls) == 1  # one set_payload call covering both ids, same text
    assert sorted(calls[0]["points"]) == [10, 11]


def test_materialize_raises_lexical_index_error_on_scroll_failure(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="scroll boom")

    _mock(monkeypatch, handler)

    with pytest.raises(lexical_index.LexicalIndexError, match="scroll"):
        lexical_index.materialize_chunk_text("http://q", "technology")


def test_materialize_raises_lexical_index_error_on_set_payload_failure(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/points/scroll"):
            return httpx.Response(
                200,
                json={
                    "result": {
                        "points": [{"id": 1, "payload": {"text": "prose"}}],
                        "next_page_offset": None,
                    }
                },
            )
        return httpx.Response(500, text="set_payload boom")

    _mock(monkeypatch, handler)

    with pytest.raises(lexical_index.LexicalIndexError, match="set_payload"):
        lexical_index.materialize_chunk_text("http://q", "technology")


# --- create_chunk_text_index ------------------------------------------------


def test_create_index_tolerates_409(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/collections/technology/index"
        body = json.loads(request.content)
        assert body["field_name"] == "chunk_text"
        assert body["field_schema"] == {"type": "text", "tokenizer": "word", "lowercase": True}
        return httpx.Response(409, text="already exists")

    _mock(monkeypatch, handler)

    lexical_index.create_chunk_text_index("http://q", "technology")  # must not raise


def test_create_index_raises_on_other_failures(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="index boom")

    _mock(monkeypatch, handler)

    with pytest.raises(lexical_index.LexicalIndexError, match="500"):
        lexical_index.create_chunk_text_index("http://q", "technology")


# --- ensure_chunk_text_index (orchestration) --------------------------------


def test_ensure_absent_materializes_creates_index_and_reprobes(monkeypatch):
    seq = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        seq.append((request.method, p))
        if p == "/collections/technology" and request.method == "GET":
            probes = len([s for s in seq if s == ("GET", p)])
            schema = {} if probes == 1 else {"chunk_text": {"data_type": "text"}}
            return httpx.Response(200, json=_collection_info(schema))
        if p.endswith("/points/scroll"):
            return httpx.Response(200, json={"result": {"points": [], "next_page_offset": None}})
        if p.endswith("/index"):
            return httpx.Response(200, json={"result": {}, "status": "ok"})
        raise AssertionError(p)

    _mock(monkeypatch, handler)

    status = lexical_index.ensure_chunk_text_index("http://q", "technology")

    assert status == "ready"
    assert ("PUT", "/collections/technology/index") in seq
    assert any(m == "POST" and p.endswith("/points/scroll") for m, p in seq)
    # exactly two status probes: the initial absent check and the final re-probe
    assert len([s for s in seq if s == ("GET", "/collections/technology")]) == 2


def test_ensure_ready_short_circuits_without_materializing(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=_collection_info({"chunk_text": {"data_type": "text"}}))

    _mock(monkeypatch, handler)

    status = lexical_index.ensure_chunk_text_index("http://q", "technology")

    assert status == "ready"
    assert calls == ["/collections/technology"]  # a single probe, nothing else


def test_ensure_returns_unavailable_when_the_probe_itself_fails(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _mock(monkeypatch, handler)

    assert lexical_index.ensure_chunk_text_index("http://q", "technology") == "unavailable"


def test_ensure_propagates_materialize_failure(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/collections/technology":
            return httpx.Response(200, json=_collection_info({}))
        if request.url.path.endswith("/points/scroll"):
            return httpx.Response(500, text="scroll boom")
        raise AssertionError(request.url.path)

    _mock(monkeypatch, handler)

    with pytest.raises(lexical_index.LexicalIndexError):
        lexical_index.ensure_chunk_text_index("http://q", "technology")
