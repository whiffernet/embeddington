"""Tests for the embedding HTTP client."""

import json

import httpx
import pytest
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
    client = EmbeddingClient(url="http://test/embed", index="technology", transport=transport)

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


@pytest.mark.asyncio
async def test_embed_batch_single_post_and_order():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["n"] = captured.get("n", 0) + 1
        captured["body"] = json.loads(request.content)
        texts = captured["body"]["texts"]
        return httpx.Response(
            200, json={"embeddings": [[float(i)] * 1024 for i in range(len(texts))]}
        )

    client = EmbeddingClient(
        url="http://test-embed/embed",
        index="technology",
        transport=httpx.MockTransport(handler),
    )
    vecs = await client.embed_batch(["a", "b", "c"])
    assert captured["n"] == 1  # ONE request for the whole batch
    assert captured["body"] == {"texts": ["a", "b", "c"], "index": "technology"}
    assert [v[0] for v in vecs] == [0.0, 1.0, 2.0]  # order preserved
    await client.close()


@pytest.mark.asyncio
async def test_embed_batch_empty_makes_no_request():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request expected")

    client = EmbeddingClient(url="http://test-embed/embed", transport=httpx.MockTransport(handler))
    assert await client.embed_batch([]) == []
    await client.close()


@pytest.mark.asyncio
async def test_embed_batch_count_mismatch_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[0.0] * 1024]})  # 1 for 2

    client = EmbeddingClient(url="http://test-embed/embed", transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingError, match="count"):
        await client.embed_batch(["a", "b"])
    await client.close()


@pytest.mark.asyncio
async def test_embed_batch_bad_dim_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[0.0] * 8]})

    client = EmbeddingClient(url="http://test-embed/embed", transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingError, match="dim"):
        await client.embed_batch(["a"])
    await client.close()


@pytest.mark.asyncio
async def test_embed_batch_second_call_hits_cache():
    calls: dict = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        texts = json.loads(request.content)["texts"]
        return httpx.Response(
            200, json={"embeddings": [[float(i)] * 1024 for i in range(len(texts))]}
        )

    client = EmbeddingClient(url="http://test-embed/embed", transport=httpx.MockTransport(handler))
    vecs1 = await client.embed_batch(["a", "b"])
    vecs2 = await client.embed_batch(["a", "b"])
    assert calls["n"] == 1  # handler invoked once
    assert vecs2 == vecs1
    await client.close()


@pytest.mark.asyncio
async def test_embed_batch_partial_hit_posts_only_misses():
    captured: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["texts"]
        captured.append(texts)
        return httpx.Response(200, json={"embeddings": [[float(ord(t[0]))] * 1024 for t in texts]})

    client = EmbeddingClient(url="http://test-embed/embed", transport=httpx.MockTransport(handler))
    await client.embed_batch(["a", "b"])
    vecs = await client.embed_batch(["b", "c"])
    assert captured[1] == ["c"]  # only the miss was posted
    assert [v[0] for v in vecs] == [float(ord("b")), float(ord("c"))]
    await client.close()


@pytest.mark.asyncio
async def test_embed_batch_cache_disabled_when_zero():
    calls: dict = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        texts = json.loads(request.content)["texts"]
        return httpx.Response(
            200, json={"embeddings": [[float(i)] * 1024 for i in range(len(texts))]}
        )

    client = EmbeddingClient(
        url="http://test-embed/embed", transport=httpx.MockTransport(handler), cache_size=0
    )
    await client.embed_batch(["a", "b"])
    await client.embed_batch(["a", "b"])
    assert calls["n"] == 2  # caching disabled -> two POSTs
    await client.close()


@pytest.mark.asyncio
async def test_embed_batch_failed_call_not_cached():
    calls: dict = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="boom")
        texts = json.loads(request.content)["texts"]
        return httpx.Response(
            200, json={"embeddings": [[float(i)] * 1024 for i in range(len(texts))]}
        )

    client = EmbeddingClient(url="http://test-embed/embed", transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingError):
        await client.embed_batch(["a", "b"])
    vecs = await client.embed_batch(["a", "b"])
    assert calls["n"] == 2  # second call re-posted, not served from a poisoned cache
    assert len(vecs) == 2
    await client.close()


@pytest.mark.asyncio
async def test_embed_batch_lru_eviction():
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["texts"]
        calls.append(texts)
        return httpx.Response(200, json={"embeddings": [[float(ord(t[0]))] * 1024 for t in texts]})

    client = EmbeddingClient(
        url="http://test-embed/embed", transport=httpx.MockTransport(handler), cache_size=2
    )
    await client.embed_batch(["a", "b"])
    await client.embed_batch(["c"])  # evicts "a" (LRU)
    await client.embed_batch(["a"])  # must re-post
    assert calls[-1] == ["a"]
    await client.close()


@pytest.mark.asyncio
async def test_embed_batch_index_partitions_cache():
    calls: dict = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        texts = json.loads(request.content)["texts"]
        return httpx.Response(
            200, json={"embeddings": [[float(i)] * 1024 for i in range(len(texts))]}
        )

    transport = httpx.MockTransport(handler)
    client_x = EmbeddingClient(url="http://test-embed/embed", index="x", transport=transport)
    client_none = EmbeddingClient(url="http://test-embed/embed", index=None, transport=transport)

    await client_x.embed_batch(["shared"])
    await client_x.embed_batch(["shared"])  # cache hit within client_x
    await client_none.embed_batch(["shared"])  # different index -> miss
    await client_none.embed_batch(["shared"])  # cache hit within client_none

    assert calls["n"] == 2  # one POST per client/index
    await client_x.close()
    await client_none.close()
