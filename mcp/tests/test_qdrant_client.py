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
