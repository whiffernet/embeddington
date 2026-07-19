"""Tests for the Qdrant client."""

import json

import httpx
import pytest
from qdrant_client import (
    QdrantError,
    QdrantSearchClient,
    _extract_payload_text,
)


@pytest.mark.asyncio
async def test_search_hits_scoped_collection_path():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = request.read()
        # Real Qdrant shape: result is a LIST of points directly, not a dict.
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "id": "1",
                        "score": 0.9,
                        "payload": {"text": "hello", "source": "x.md"},
                    },
                ],
                "status": "ok",
            },
        )

    transport = httpx.MockTransport(handler)
    client = QdrantSearchClient(
        url="http://test:6333",
        collection="technology",
        transport=transport,
    )

    results = await client.search(vector=[0.1] * 1024, limit=5)

    assert captured["path"] == "/collections/technology/points/search"
    assert len(results) == 1
    assert results[0]["score"] == pytest.approx(0.9)
    assert results[0]["text"] == "hello"


@pytest.mark.asyncio
async def test_search_raises_qdrant_error_on_500():
    def handler(request):
        return httpx.Response(500, text="server error")

    transport = httpx.MockTransport(handler)
    client = QdrantSearchClient(
        url="http://test:6333",
        collection="technology",
        transport=transport,
    )

    with pytest.raises(QdrantError, match="500"):
        await client.search(vector=[0.1] * 1024, limit=5)


@pytest.mark.asyncio
async def test_can_read_collection_returns_false_on_404():
    def handler(request):
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = QdrantSearchClient(
        url="http://test:6333",
        collection="technology",
        transport=transport,
    )

    assert await client.can_read_collection("nonexistent") is False


@pytest.mark.asyncio
async def test_can_read_collection_returns_true_on_200():
    def handler(request):
        return httpx.Response(200, json={"result": [], "status": "ok"})

    transport = httpx.MockTransport(handler)
    client = QdrantSearchClient(
        url="http://test:6333",
        collection="technology",
        transport=transport,
    )

    assert await client.can_read_collection("technology") is True


# --- _extract_payload_text + LlamaIndex-shape payloads -------------------


def test_extract_payload_text_prefers_top_level():
    payload = {"text": "direct text", "_node_content": '{"text": "should not win"}'}
    assert _extract_payload_text(payload) == "direct text"


def test_extract_payload_text_falls_back_to_node_content():
    node = {
        "id_": "n1",
        "text": "the real chunk prose",
        "metadata": {"file_name": "x.pdf"},
        "mimetype": "text/plain",
    }
    payload = {"text": "", "_node_content": json.dumps(node), "file_name": "x.pdf"}
    assert _extract_payload_text(payload) == "the real chunk prose"


def test_extract_payload_text_returns_empty_when_neither_present():
    assert _extract_payload_text({"file_name": "x.pdf"}) == ""


def test_extract_payload_text_handles_malformed_node_content():
    payload = {"text": "", "_node_content": "{not valid json"}
    assert _extract_payload_text(payload) == ""


@pytest.mark.asyncio
async def test_search_extracts_text_from_llamaindex_node_content():
    """Bake-off post-mortem: LlamaIndex stores chunk text inside
    `payload._node_content` as a stringified TextNode blob; top-level
    `text` is empty. Consumer must see populated `text` and no
    `_node_content` leak in metadata."""
    node = {
        "id_": "abc",
        "text": "Workflow Studio replaces legacy workflows on Zurich upgrade.",
        "metadata": {"file_name": "zurich-release-notes.pdf", "release": "zurich"},
        "mimetype": "text/plain",
    }

    def handler(request):
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "id": "abc",
                        "score": 0.74,
                        "payload": {
                            "text": "",  # top-level text is empty (the bug)
                            "source": "",
                            "_node_content": json.dumps(node),
                            "file_name": "zurich-release-notes.pdf",
                            "release": "zurich",
                        },
                    },
                ],
                "status": "ok",
            },
        )

    transport = httpx.MockTransport(handler)
    client = QdrantSearchClient(
        url="http://test:6333",
        collection="technology",
        transport=transport,
    )

    results = await client.search(vector=[0.1] * 1024, limit=5)

    assert len(results) == 1
    chunk = results[0]
    assert chunk["text"].startswith("Workflow Studio replaces legacy workflows")
    assert chunk["source"] == "zurich-release-notes.pdf"  # file_name fallback
    assert "_node_content" not in chunk["metadata"]
    assert "text" not in chunk["metadata"]
    assert chunk["metadata"]["release"] == "zurich"


@pytest.mark.asyncio
async def test_search_drops_chunks_with_no_recoverable_text():
    """If neither `text` nor a parseable `_node_content` is present, the
    chunk is dropped rather than returned with an empty text field."""

    def handler(request):
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "id": "good",
                        "score": 0.9,
                        "payload": {"text": "real prose", "source": "a.md"},
                    },
                    {
                        "id": "empty",
                        "score": 0.5,
                        "payload": {"text": "", "source": "b.md"},
                    },
                ],
                "status": "ok",
            },
        )

    transport = httpx.MockTransport(handler)
    client = QdrantSearchClient(
        url="http://test:6333",
        collection="technology",
        transport=transport,
    )

    results = await client.search(vector=[0.1] * 1024, limit=5)

    assert len(results) == 1
    assert results[0]["id"] == "good"


# --- chunk_text surface (consumer-local materialize/index/status) --------


def _collection_info(payload_schema: dict, status: str = "green") -> dict:
    return {"result": {"status": status, "payload_schema": payload_schema}}


@pytest.mark.asyncio
async def test_chunk_text_status_ready_building_absent():
    state = {"schema": {}, "status": "green"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/collections/technology":
            return httpx.Response(200, json=_collection_info(state["schema"], state["status"]))
        raise AssertionError(request.url.path)

    c = QdrantSearchClient("http://q", "technology", transport=httpx.MockTransport(handler))
    assert await c.chunk_text_status() == "absent"
    state["schema"] = {"chunk_text": {"data_type": "text", "points": 10}}
    state["status"] = "yellow"
    assert await c.chunk_text_status() == "building"
    state["status"] = "green"
    assert await c.chunk_text_status() == "ready"
    await c.close()


@pytest.mark.asyncio
async def test_chunk_text_status_unavailable_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    c = QdrantSearchClient("http://q", "technology", transport=httpx.MockTransport(handler))
    assert await c.chunk_text_status() == "unavailable"
    await c.close()


@pytest.mark.asyncio
async def test_materialize_writes_extracted_prose_in_batches():
    calls = {"scroll": 0, "payload": []}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/points/scroll"):
            calls["scroll"] += 1
            if calls["scroll"] == 1:
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
                            "next_page_offset": None,
                        }
                    },
                )
            raise AssertionError("second scroll after next_page_offset=None")
        if request.url.path.endswith("/points/payload"):
            calls["payload"].append(body)
            return httpx.Response(200, json={"result": {}, "status": "ok"})
        raise AssertionError(request.url.path)

    c = QdrantSearchClient("http://q", "technology", transport=httpx.MockTransport(handler))
    n = await c.materialize_chunk_text()
    assert n == 2  # point 2 had no recoverable prose -> skipped
    # Two distinct texts ("prose one", "direct") -> two set_payload calls,
    # one per point, covering exactly ids {1} and {3} respectively.
    assert len(calls["payload"]) == 2
    by_ids = {
        tuple(sorted(call["points"])): call["payload"]["chunk_text"] for call in calls["payload"]
    }
    assert by_ids == {(1,): "prose one", (3,): "direct"}
    await c.close()


@pytest.mark.asyncio
async def test_ensure_absent_materializes_creates_index_and_reprobes():
    seq = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        seq.append((request.method, p))
        if p == "/collections/technology" and request.method == "GET":
            # first probe: absent; final probe: ready
            schema = (
                {}
                if len([s for s in seq if s == ("GET", p)]) == 1
                else {"chunk_text": {"data_type": "text"}}
            )
            return httpx.Response(200, json=_collection_info(schema))
        if p.endswith("/points/scroll"):
            return httpx.Response(200, json={"result": {"points": [], "next_page_offset": None}})
        if p.endswith("/index"):
            return httpx.Response(200, json={"result": {}, "status": "ok"})
        raise AssertionError(p)

    c = QdrantSearchClient("http://q", "technology", transport=httpx.MockTransport(handler))
    assert await c.ensure_chunk_text() == "ready"
    assert ("PUT", "/collections/technology/index") in seq
    assert any(m == "POST" and p.endswith("/points/scroll") for m, p in seq)
    await c.close()


@pytest.mark.asyncio
async def test_ensure_post_create_reprobe_polls_past_registration_race(monkeypatch):
    """Qdrant's index-create PUT acks (~7ms, live-observed) before its
    payload_schema registration lands a beat later — a re-probe run
    immediately after a successful create can still read "absent". Without
    polling, ensure_chunk_text would wrongly report "absent" right after a
    successful materialize+create, and the next ensure call would trigger a
    wasteful re-materialize (live-validation defect, issue #38)."""
    import qdrant_client as qc_mod

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(qc_mod.asyncio, "sleep", fake_sleep)

    state = {"created": False}
    post_create_probes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/collections/technology" and request.method == "GET":
            if not state["created"]:
                return httpx.Response(200, json=_collection_info({}))  # initial probe: absent
            post_create_probes.append(1)
            if len(post_create_probes) == 1:
                return httpx.Response(200, json=_collection_info({}))  # absent once
            return httpx.Response(
                200, json=_collection_info({"chunk_text": {"data_type": "text"}})
            )  # then ready
        if p.endswith("/points/scroll"):
            return httpx.Response(200, json={"result": {"points": [], "next_page_offset": None}})
        if p.endswith("/index"):
            state["created"] = True
            return httpx.Response(200, json={"result": {}, "status": "ok"})
        raise AssertionError(p)

    c = QdrantSearchClient("http://q", "technology", transport=httpx.MockTransport(handler))
    assert await c.ensure_chunk_text() == "ready"
    assert len(post_create_probes) == 2
    assert sleep_calls == [0.5]  # exactly one sleep, between the two post-create probes
    await c.close()


@pytest.mark.asyncio
async def test_search_match_text_adds_filter_and_plain_search_does_not():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"result": []})

    c = QdrantSearchClient("http://q", "technology", transport=httpx.MockTransport(handler))
    await c.search([0.0] * 3, limit=5)
    await c.search([0.0] * 3, limit=5, match_text="cmdb_rel_ci")
    assert "filter" not in bodies[0]
    assert bodies[1]["filter"]["must"][0] == {"key": "chunk_text", "match": {"text": "cmdb_rel_ci"}}
    await c.close()
