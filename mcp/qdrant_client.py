"""Qdrant client for embeddington.

v1: code-level scoping only. The collection name is hardcoded at construction
time and never accepted from external input. No JWT in v1 (Qdrant has no
auth enabled — see spec §5 for the deferral rationale and the future
JWT-enabled version).

Also exposes a consumer-local ``chunk_text`` payload surface (status probe,
one-off materialization, full-text index) used for lexical search — this
write surface touches only the consumer's own collection and is never part
of the published baseline/diff snapshots.
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

    async def search(
        self, vector: list[float], limit: int = 10, match_text: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Search the scoped collection by vector similarity.

        Args:
            vector: Query embedding.
            limit: Max number of results.
            match_text: When given, restricts results to chunks whose
                ``chunk_text`` payload field contains this text (the lexical
                lane). Requires `ensure_chunk_text` to have reached "ready".

        Returns:
            List of `{id, score, text, source, metadata}` dicts.

        Raises:
            QdrantError: On any non-200 response or transport failure.
        """
        path = f"/collections/{self.collection}/points/search"
        body: dict[str, Any] = {"vector": vector, "limit": limit, "with_payload": True}
        if match_text:
            body["filter"] = {"must": [{"key": "chunk_text", "match": {"text": match_text}}]}
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
                        k: v
                        for k, v in payload.items()
                        if k not in ("text", "_node_content", "chunk_text")
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

    async def chunk_text_status(self) -> str:
        """State of the consumer-local chunk_text full-text index.

        Returns:
            "ready" if the ``chunk_text`` field is indexed and the
            collection status is green; "building" if the field exists but
            the collection isn't green yet; "absent" if the field doesn't
            exist; "unavailable" if the probe itself failed. Anything but
            "ready" means the lexical lane must degrade (spec §5 PR 4/§6).
        """
        client = await self._http()
        try:
            resp = await client.get(f"{self.url}/collections/{self.collection}")
        except httpx.HTTPError:
            return "unavailable"
        if resp.status_code != 200:
            return "unavailable"
        result = resp.json().get("result", {}) or {}
        schema = result.get("payload_schema", {}) or {}
        if "chunk_text" not in schema:
            return "absent"
        return "ready" if result.get("status") == "green" else "building"

    async def materialize_chunk_text(self, batch: int = 256, max_points: int | None = None) -> int:
        """Copy chunk prose into a first-class chunk_text payload field.

        The shared collection stores prose inside the stringified
        ``_node_content`` blob (top-level ``text`` is empty), which a full-text
        index cannot usefully target — so the prose is materialized once,
        batch by batch, into ``chunk_text`` on the consumer's own collection.
        Points with no recoverable text (see `_extract_payload_text`) are
        skipped and not counted.

        Args:
            batch: Scroll page size.
            max_points: Optional cap on the number of points written; the
                scroll stops once this many have been written.

        Returns:
            Number of points written.

        Raises:
            QdrantError: On any non-200 response from scroll or set_payload.
        """
        client = await self._http()
        written = 0
        offset: Any = None
        while True:
            body: dict[str, Any] = {
                "limit": batch,
                "with_payload": ["text", "_node_content"],
                "filter": {"must": [{"is_empty": {"key": "chunk_text"}}]},
            }
            if offset is not None:
                body["offset"] = offset
            resp = await client.post(
                f"{self.url}/collections/{self.collection}/points/scroll", json=body
            )
            if resp.status_code != 200:
                raise QdrantError(f"scroll failed: {resp.status_code}: {resp.text[:200]}")
            result = resp.json().get("result", {}) or {}
            points = result.get("points", []) or []
            by_text: dict[str, list] = {}
            for p in points:
                text = _extract_payload_text(p.get("payload", {}) or {})
                if text:
                    by_text.setdefault(text, []).append(p.get("id"))
            for text, ids in by_text.items():
                presp = await client.post(
                    f"{self.url}/collections/{self.collection}/points/payload",
                    json={"payload": {"chunk_text": text}, "points": ids},
                )
                if presp.status_code != 200:
                    raise QdrantError(
                        f"set_payload failed: {presp.status_code}: {presp.text[:200]}"
                    )
                written += len(ids)
            offset = result.get("next_page_offset")
            if offset is None or (max_points is not None and written >= max_points):
                return written

    async def create_chunk_text_index(self) -> None:
        """Create the full-text index on chunk_text (idempotent-tolerant).

        Raises:
            QdrantError: On any response other than 200 (created) or 409
                (already exists).
        """
        client = await self._http()
        resp = await client.put(
            f"{self.url}/collections/{self.collection}/index",
            json={
                "field_name": "chunk_text",
                "field_schema": {"type": "text", "tokenizer": "word", "lowercase": True},
            },
        )
        if resp.status_code not in (200, 409):
            raise QdrantError(f"index create failed: {resp.status_code}: {resp.text[:200]}")

    async def ensure_chunk_text(self, materialize_batch: int = 256) -> str:
        """Ensure chunk_text exists + is indexed; return the final status.

        Runs on every server start and lazily (baseline restores recreate the
        collection and silently drop both field and index). Degraded states
        ("building"/"unavailable") are returned, not raised — the caller
        skips the lexical lane and says so in the envelope.

        Args:
            materialize_batch: Scroll page size passed to
                `materialize_chunk_text` when materialization is needed.

        Returns:
            The final `chunk_text_status()` value after ensuring.

        Raises:
            QdrantError: On transport-level failure of the materialize or
                index-create steps themselves.
        """
        status = await self.chunk_text_status()
        if status == "absent":
            n = await self.materialize_chunk_text(batch=materialize_batch)
            logger.info("chunk_text materialized on %s points", n)
            await self.create_chunk_text_index()
            status = await self.chunk_text_status()
        return status

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
