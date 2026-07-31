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


# ---------------------------------------------------------------------------
# Optional QDRANT_API_KEY (#66).
#
# The default -- no credential -- is what every install using the bundled
# compose file runs, and these tests exist to guarantee that path is untouched.
# `test_request_shape_is_unchanged_when_credential_absent` is the one that
# actually protects existing users: it pins URL, method and body, so a
# regression here shows up as a failing test rather than as a broken install.
# ---------------------------------------------------------------------------

_KEY = "test-api-key-value"


def _capture(seen):
    def handler(request):
        seen["headers"] = {k.lower(): v for k, v in request.headers.items()}
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["body"] = request.content
        return httpx.Response(200, json={"result": []})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_no_api_key_header_when_credential_absent():
    """The default path, and the one every keyless install takes."""
    seen = {}
    c = QdrantSearchClient("http://x:6333", "technology", transport=_capture(seen))
    await c.search([0.1] * 4)
    assert "api-key" not in seen["headers"]


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n", "  \t\n "])
async def test_no_api_key_header_when_credential_is_blank(blank):
    """Blank must behave as absent, never as an empty credential.

    An empty `api-key` is rejected by an authenticated Qdrant, and a
    whitespace-only string is truthy in Python -- so a stray space in a config
    file would otherwise turn into a puzzling 401 rather than the keyless
    behaviour the user expected.
    """
    seen = {}
    c = QdrantSearchClient("http://x:6333", "technology", api_key=blank, transport=_capture(seen))
    await c.search([0.1] * 4)
    assert "api-key" not in seen["headers"]


@pytest.mark.asyncio
async def test_exactly_one_api_key_header_when_credential_set():
    seen = {}
    c = QdrantSearchClient("http://x:6333", "technology", api_key=_KEY, transport=_capture(seen))
    await c.search([0.1] * 4)
    assert seen["headers"].get("api-key") == _KEY


@pytest.mark.asyncio
async def test_surrounding_whitespace_is_stripped():
    """A pasted key usually carries a trailing newline."""
    seen = {}
    c = QdrantSearchClient(
        "http://x:6333", "technology", api_key=f"  {_KEY}\n", transport=_capture(seen)
    )
    await c.search([0.1] * 4)
    assert seen["headers"].get("api-key") == _KEY


@pytest.mark.asyncio
async def test_request_shape_is_unchanged_when_credential_absent():
    """URL, method and body must match the pre-change baseline exactly.

    This is the compatibility guarantee for existing installs, stated as an
    executable assertion rather than an intention.
    """
    seen = {}
    c = QdrantSearchClient("http://x:6333", "technology", transport=_capture(seen))
    await c.search([0.1] * 4, limit=7)
    assert seen["method"] == "POST"
    assert seen["url"] == "http://x:6333/collections/technology/points/search"
    assert b'"limit": 7' in seen["body"] or b'"limit":7' in seen["body"]


@pytest.mark.asyncio
async def test_can_read_collection_is_false_on_401():
    """A wrong or missing key must read as 'not reachable', not crash.

    `can_read_collection` drives the startup check, so a 401 needs to surface as
    a clean False and a legible refusal rather than an opaque traceback.
    """

    def handler(request):
        return httpx.Response(401, json={"status": {"error": "unauthorized"}})

    c = QdrantSearchClient("http://x:6333", "technology", transport=httpx.MockTransport(handler))
    assert await c.can_read_collection("technology") is False
