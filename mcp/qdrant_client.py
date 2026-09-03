"""Qdrant client for embeddington.

v1: code-level scoping only. The collection name is hardcoded at construction
time and never accepted from external input. No JWT in v1 (Qdrant has no
auth enabled — see spec §5 for the deferral rationale and the future
JWT-enabled version).

Also exposes a read-only ``chunk_text_status`` probe used by the lexical
search lane. The ``chunk_text`` payload field and its full-text index are
built by the consumer install/update flow (`consumer/lexical_index.py`),
never by this client — this module issues no Qdrant writes.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

try:
    from .probe import RETRYABLE_TRANSPORT_ERRORS, probe_with_retry
except ImportError:  # pragma: no cover — flat-layout import fallback
    from probe import (  # type: ignore[no-redef] # noqa: F401
        RETRYABLE_TRANSPORT_ERRORS,
        probe_with_retry,
    )

logger = logging.getLogger("embeddington.qdrant")

__all__ = [
    "QdrantError",
    "QdrantSearchClient",
    "RETRYABLE_TRANSPORT_ERRORS",
]


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
        api_key: Optional Qdrant API key, for an instance that requires
            authentication. Absent, empty and whitespace-only are equivalent and
            send no ``api-key`` header at all, leaving a keyless install's
            requests byte-identical. Keyword-optional, so existing callers are
            unaffected.
        transport: Optional httpx transport (used by tests).
    """

    def __init__(
        self,
        url: str,
        collection: str,
        timeout: float = 30.0,
        api_key: Optional[str] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.collection = collection
        self.timeout = timeout
        # Normalised once, here, so every downstream check is a plain `is None`
        # and a whitespace-only value can never reach the wire as a credential.
        self.api_key = (api_key or "").strip() or None
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            # No credential means NO header, not an empty one — an empty api-key
            # is rejected by an authenticated Qdrant, while sending nothing is
            # the supported keyless path.
            headers = {"api-key": self.api_key} if self.api_key else None
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers=headers,
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
                lane). Requires `chunk_text_status` to report "ready".

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

    async def probe_collection(
        self,
        collection: str,
        retries: int = 0,
        backoff: float = 1.0,
        timeout: Optional[float] = None,
        deadline: Optional[float] = None,
    ) -> tuple[bool, str]:
        """Probe whether the configured Qdrant URL can serve this collection.

        Backs the startup positive-reachability check in
        `_isolation_sanity_check`. The probe succeeds iff a /search call
        returns 200. In a future JWT-enabled version, it also serves as the
        isolation deny-check.

        Retries (with exponential backoff) apply ONLY to
        `RETRYABLE_TRANSPORT_ERRORS` — the request never reached a server or
        the server never answered, the fingerprint of a Qdrant host that is
        down or still starting up. Anything that produced a response, or that
        failed for a permanent reason (a typo'd URL scheme, a redirect loop),
        is reported on the first attempt: retrying cannot change the outcome
        and would only delay the error.

        The returned detail names the actual failure so an operator is not
        left guessing between "the host was down" and "the credential was
        rejected" — the two were indistinguishable in the 2026-08-27
        embeddington-prod incident, which is what made it expensive to
        diagnose.

        Args:
            collection: Collection name to probe.
            retries: Additional attempts after a retryable transport failure.
                0 (default) preserves single-shot behavior.
            backoff: Seconds to wait before the first retry; doubles on each
                subsequent attempt.
            timeout: Per-attempt timeout override. Defaults to the client's
                own timeout, which is sized for real queries and is usually
                far too long for a liveness probe.
            deadline: Total seconds to spend across all attempts. Retrying
                stops once the next backoff would exceed this, bounding the
                worst case regardless of `retries` — startup must not outlast
                the MCP client's own initialization timeout.

        Returns:
            ``(ok, detail)``. ``detail`` is an operator-facing explanation of
            the failure, and is empty when ``ok`` is True.
        """
        client = await self._http()
        path = f"/collections/{collection}/points/search"
        request_timeout = self.timeout if timeout is None else timeout

        async def attempt() -> tuple[bool, str]:
            resp = await client.post(
                f"{self.url}{path}",
                json={"vector": [0.0] * 1024, "limit": 1},
                timeout=request_timeout,
            )
            if resp.status_code == 200:
                return True, ""
            return False, (
                f"{self.url} answered HTTP {resp.status_code} — collection "
                f"'{collection}' is missing, or the credential was rejected "
                f"(check QDRANT_API_KEY)"
            )

        return await probe_with_retry(
            attempt,
            target=self.url,
            what="Qdrant",
            retries=retries,
            backoff=backoff,
            deadline=deadline,
        )

    async def can_read_collection(
        self,
        collection: str,
        retries: int = 0,
        backoff: float = 1.0,
        timeout: Optional[float] = None,
        deadline: Optional[float] = None,
    ) -> bool:
        """Boolean form of `probe_collection`, for callers that want no detail.

        Args:
            collection: Collection name to probe.
            retries: See `probe_collection`.
            backoff: See `probe_collection`.
            timeout: See `probe_collection`.
            deadline: See `probe_collection`.

        Returns:
            True iff the collection is readable.
        """
        ok, _ = await self.probe_collection(
            collection,
            retries=retries,
            backoff=backoff,
            timeout=timeout,
            deadline=deadline,
        )
        return ok

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

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
