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
from typing import Optional

import httpx

logger = logging.getLogger("claudegraph.embedding")

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
    """

    def __init__(
        self,
        url: str,
        index: Optional[str] = None,
        timeout: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.url = url
        self.index = index
        self.timeout = timeout
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout, transport=self._transport
            )
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

    async def close(self) -> None:
        """Close the underlying HTTP client connection.

        Should be called when the client is no longer needed to release resources.
        """
        if self._client and not self._client.is_closed:
            await self._client.aclose()
