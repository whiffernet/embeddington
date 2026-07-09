"""Qdrant client for embeddington.

v1: code-level scoping only. The collection name is hardcoded at construction
time and never accepted from external input. No JWT in v1 (Qdrant has no
auth enabled — see spec §5 for the deferral rationale and the future
JWT-enabled version).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger("embeddington.qdrant")


class QdrantError(Exception):
    """Raised on any Qdrant HTTP failure."""


def _extract_payload_text(payload: dict[str, Any]) -> str:
    """Return the chunk's prose text from a Qdrant payload.

    Prefers the top-level ``text`` field. Falls back to parsing the
    stringified ``_node_content`` blob that LlamaIndex stores when a
    collection is ingested through the LlamaIndex Qdrant adapter — the
    actual chunk text lives inside that blob's ``text`` key, and the
    top-level ``text`` is empty.

    Args:
        payload: The ``payload`` dict from a Qdrant point result.

    Returns:
        The chunk text, or an empty string if no text could be recovered.
    """
    text = payload.get("text")
    if text:
        return text
    blob = payload.get("_node_content")
    if not blob:
        return ""
    try:
        parsed = json.loads(blob) if isinstance(blob, str) else blob
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(parsed, dict):
        return ""
    return parsed.get("text", "") or ""


class QdrantSearchClient:
    """Async Qdrant client scoped to a single collection.

    Args:
        url: Qdrant base URL (e.g. http://localhost:6333).
        collection: The single collection this client may read. Hardcoded
            into every request path; never overridden by callers.
        timeout: Request timeout in seconds.
        transport: Optional httpx transport (used by tests).
    """

    def __init__(
        self,
        url: str,
        collection: str,
        timeout: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.collection = collection
        self.timeout = timeout
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            # v1: no auth headers. When JWT lands, add headers={"api-key": jwt} here.
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                transport=self._transport,
            )
        return self._client

    async def search(self, vector: list[float], limit: int = 10) -> list[dict[str, Any]]:
        """Search the scoped collection by vector similarity.

        Returns:
            List of `{id, score, text, source, metadata}` dicts.

        Raises:
            QdrantError: On any non-200 response or transport failure.
        """
        path = f"/collections/{self.collection}/points/search"
        body = {"vector": vector, "limit": limit, "with_payload": True}
        return await self._post_search(path, body)

    async def _post_search(self, path: str, body: dict) -> list[dict[str, Any]]:
        client = await self._http()
        try:
            resp = await client.post(f"{self.url}{path}", json=body)
        except httpx.HTTPError as exc:
            raise QdrantError(f"qdrant request failed: {exc}") from exc

        if resp.status_code != 200:
            raise QdrantError(f"qdrant returned {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        # Qdrant returns {"result": [...points], "status": "ok"} — `result` is
        # the list of points directly, NOT a dict with a "points" key.
        points = data.get("result", [])
        if not isinstance(points, list):
            points = []  # defensive: future Qdrant versions may change shape
        chunks: list[dict[str, Any]] = []
        for p in points:
            payload = p.get("payload", {}) or {}
            text = _extract_payload_text(payload)
            # Don't return chunks with no recoverable text — the consumer
            # would silently treat them as "no RAG content available".
            if not text:
                continue
            chunks.append(
                {
                    "id": str(p.get("id")),
                    "score": p.get("score", 0.0),
                    "text": text,
                    "source": (payload.get("source") or payload.get("file_name") or ""),
                    "metadata": {
                        k: v for k, v in payload.items() if k not in ("text", "_node_content")
                    },
                }
            )
        return chunks

    async def can_read_collection(self, collection: str) -> bool:
        """Probe whether the configured Qdrant URL can serve this collection.

        Used by the startup positive-reachability check in `_isolation_sanity_check`. Returns True
        iff a /search call returns 200. In a future JWT-enabled version,
        this also serves as the isolation deny-check.

        Args:
            collection: Collection name to probe.
        """
        client = await self._http()
        path = f"/collections/{collection}/points/search"
        try:
            resp = await client.post(
                f"{self.url}{path}",
                json={"vector": [0.0] * 1024, "limit": 1},
            )
        except httpx.HTTPError:
            return False
        return resp.status_code == 200

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
