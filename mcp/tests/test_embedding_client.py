"""Tests for the embedding HTTP client."""

import json

import pytest
import httpx

from embedding_client import EmbeddingClient, EmbeddingError


@pytest.mark.asyncio
async def test_embed_single_query_returns_vector():
    fake_response = {"embeddings": [[0.1] * 1024]}

    def handler(request):
        assert request.url.path == "/embed"
        body = request.read().decode()
        assert '"texts"' in body
        return httpx.Response(200, json=fake_response)

    transport = httpx.MockTransport(handler)
    client = EmbeddingClient(url="http://test/embed", transport=transport)

    vec = await client.embed("incident management")
    assert len(vec) == 1024
    assert vec[0] == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_embed_sends_index_when_set():
    """index= routes to the per-index embedder configured for `technology`."""
    fake_response = {"embeddings": [[0.1] * 1024]}

    def handler(request):
        body = json.loads(request.read().decode())
        assert body["index"] == "technology"
        return httpx.Response(200, json=fake_response)

    transport = httpx.MockTransport(handler)
    client = EmbeddingClient(
        url="http://test/embed", index="technology", transport=transport
    )

    vec = await client.embed("incident management")
    assert len(vec) == 1024


@pytest.mark.asyncio
async def test_embed_omits_index_when_none():
    """No index= key is sent when index is unset (uses default embedder)."""
    fake_response = {"embeddings": [[0.1] * 1024]}

    def handler(request):
        body = json.loads(request.read().decode())
        assert "index" not in body
        return httpx.Response(200, json=fake_response)

    transport = httpx.MockTransport(handler)
    client = EmbeddingClient(url="http://test/embed", transport=transport)

    vec = await client.embed("incident management")
    assert len(vec) == 1024


@pytest.mark.asyncio
async def test_embed_raises_on_http_error():
    def handler(request):
        return httpx.Response(503, text="service unavailable")

    transport = httpx.MockTransport(handler)
    client = EmbeddingClient(url="http://test/embed", transport=transport)

    with pytest.raises(EmbeddingError):
        await client.embed("anything")


@pytest.mark.asyncio
async def test_embed_raises_on_unexpected_dim():
    fake_response = {"embeddings": [[0.1] * 512]}  # wrong dim

    def handler(request):
        return httpx.Response(200, json=fake_response)

    transport = httpx.MockTransport(handler)
    client = EmbeddingClient(url="http://test/embed", transport=transport)

    with pytest.raises(EmbeddingError, match="dim"):
        await client.embed("anything")
