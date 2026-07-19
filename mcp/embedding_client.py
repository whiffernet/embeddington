"""Async HTTP client for the LlamaIndex /embed endpoint.

Embeds a query string into a 1024-dim vector. The llamaindex /embed
endpoint is *per-index*: passing an ``index`` name routes through the
embedder configured for that collection. Query vectors must be produced
by the same model used to build the target Qdrant collection, or
similarity search returns orthogonal garbage.
Raises EmbeddingError on any HTTP failure or unexpected response shape.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Optional

import httpx

logger = logging.getLogger("embeddington.embedding")

EXPECTED_DIM = 1024


class EmbeddingError(Exception):
    """Raised when embedding fails for any reason."""


class EmbeddingClient:
    """Async client for the LlamaIndex /embed endpoint.

    Args:
        url: Full URL of the /embed endpoint (e.g. http://localhost:8100/embed).
        index: Per-index name routing the request to the matching embedder
            (e.g. "technology"). When None, the endpoint uses its default
            embedder. Must match the model used to embed the target Qdrant
            collection.
        timeout: Request timeout in seconds.
        transport: Optional httpx transport (used by tests for mocking).
        cache_size: Max entries in the in-process embed_batch LRU cache,
            keyed by (index, text). 0 disables caching entirely (every
            call POSTs all texts). Does not affect embed(), which is
            always uncached.
    """

    def __init__(
        self,
        url: str,
        index: Optional[str] = None,
        timeout: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
        cache_size: int = 4096,
    ) -> None:
        self.url = url
        self.index = index
        self.timeout = timeout
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        self._cache_size = cache_size
        self._cache: OrderedDict[tuple[Optional[str], str], list[float]] = OrderedDict()

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout, transport=self._transport)
        return self._client

    async def embed(self, text: str) -> list[float]:
        """Embed a single text and return its vector.

        Args:
            text: The query string to embed.

        Returns:
            1024-dimensional vector as a list of floats.

        Raises:
            EmbeddingError: On HTTP error or malformed response.
        """
        client = await self._http()
        body: dict[str, object] = {"texts": [text]}
        if self.index is not None:
            body["index"] = self.index
        try:
            resp = await client.post(self.url, json=body)
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"embedding request failed: {exc}") from exc

        if resp.status_code != 200:
            raise EmbeddingError(
                f"embedding endpoint returned {resp.status_code}: {resp.text[:200]}"
            )

        try:
            data = resp.json()
            vec = data["embeddings"][0]
        except (KeyError, IndexError, ValueError) as exc:
            raise EmbeddingError(f"malformed embedding response: {exc}") from exc

        if len(vec) != EXPECTED_DIM:
            raise EmbeddingError(
                f"unexpected embedding dim: got {len(vec)}, expected {EXPECTED_DIM}"
            )
        return vec

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed many texts, preserving order, in AT MOST one request.

        Results are served from a bounded in-process LRU cache keyed by
        (index, text) when available; only cache misses are POSTed (a
        single request covers all misses, or none is sent if everything
        hits). Failed requests are never cached. Pass cache_size=0 at
        construction to disable caching and always POST every text.

        Args:
            texts: Query/quote strings to embed. An empty list short-circuits
                to [] without a network call.

        Returns:
            One 1024-dim vector per input text, in input order.

        Raises:
            EmbeddingError: On HTTP error, malformed response, count mismatch,
                or wrong embedding dimension.
        """
        if not texts:
            return []

        if self._cache_size == 0:
            miss_texts = texts
        else:
            miss_texts = []
            for text in texts:
                key = (self.index, text)
                if key in self._cache:
                    self._cache.move_to_end(key)
                else:
                    miss_texts.append(text)

        miss_vecs: dict[str, list[float]] = {}
        if miss_texts:
            client = await self._http()
            body: dict[str, object] = {"texts": miss_texts}
            if self.index is not None:
                body["index"] = self.index
            try:
                resp = await client.post(self.url, json=body)
            except httpx.HTTPError as exc:
                raise EmbeddingError(f"embedding request failed: {exc}") from exc
            if resp.status_code != 200:
                raise EmbeddingError(
                    f"embedding endpoint returned {resp.status_code}: {resp.text[:200]}"
                )
            try:
                vecs = resp.json()["embeddings"]
            except (KeyError, ValueError) as exc:
                raise EmbeddingError(f"malformed embedding response: {exc}") from exc
            if len(vecs) != len(miss_texts):
                raise EmbeddingError(
                    f"embedding count mismatch: got {len(vecs)}, expected {len(miss_texts)}"
                )
            for v in vecs:
                if len(v) != EXPECTED_DIM:
                    raise EmbeddingError(
                        f"unexpected embedding dim: got {len(v)}, expected {EXPECTED_DIM}"
                    )
            miss_vecs = dict(zip(miss_texts, vecs))

            if self._cache_size > 0:
                for text, vec in miss_vecs.items():
                    key = (self.index, text)
                    self._cache[key] = vec
                    self._cache.move_to_end(key)
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)

        if self._cache_size == 0:
            return [miss_vecs[t] for t in texts]
        return [miss_vecs[t] if t in miss_vecs else self._cache[(self.index, t)] for t in texts]

    async def close(self) -> None:
        """Close the underlying HTTP client connection.

        Should be called when the client is no longer needed to release resources.
        """
        if self._client and not self._client.is_closed:
            await self._client.aclose()
