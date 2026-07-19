"""Consumer-side warm-up twin of the MCP's authoritative chunk_text lazy-ensure.

``mcp/qdrant_client.py``'s ``QdrantSearchClient.ensure_chunk_text`` (async) is the
per-start authoritative lazy-ensure: every MCP server start probes
``chunk_text_status`` and, if absent, materializes the ``chunk_text`` payload field
out of ``_node_content`` and builds its full-text index before the lexical search
lane is usable. A baseline restore recreates the Qdrant collection from a bare
vector snapshot, so it always comes back without the field or the index -- the
first MCP start after a restore would otherwise pay the full ~150k-point
materialization inline, blocking the first request.

This module is the SYNC twin, run once at the end of a baseline import (and via the
standalone ``embeddington-consume ensure-index`` command) so that cost is paid
during the restore -- already slow, already-awaited-for -- instead of at the first
MCP request. The REST bodies mirror ``mcp/qdrant_client.py`` exactly; consumer/ must
not import mcp/ (separate packages, no shared runtime), hence the duplication.

Writes here touch only the consumer's own local Qdrant collection -- never part of
the published baseline/diff snapshots.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from embeddington.errors import EmbeddingtonError


class LexicalIndexError(EmbeddingtonError):
    """Raised when materialize or index-create fails at the transport level."""


def _client(timeout: float) -> httpx.Client:
    """Build the httpx client used for every request.

    The sole seam: tests monkeypatch this to inject an ``httpx.MockTransport``
    instead of hitting the network.

    Args:
        timeout: Request timeout in seconds.

    Returns:
        A fresh ``httpx.Client``.
    """
    return httpx.Client(timeout=timeout)


def _extract_payload_text(payload: dict[str, Any]) -> str:
    """Return the chunk's prose text from a Qdrant payload.

    Prefers the top-level ``text`` field, else parses the stringified
    ``_node_content`` blob LlamaIndex stores and takes its ``text`` key.
    Mirrors ``mcp/qdrant_client.py::_extract_payload_text``.

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


def chunk_text_status(url: str, collection: str, timeout: float = 30.0) -> str:
    """State of the consumer-local chunk_text full-text index.

    Args:
        url: Qdrant base URL (e.g. http://localhost:6333).
        collection: Collection to probe.
        timeout: Request timeout in seconds.

    Returns:
        "ready" if the ``chunk_text`` field is indexed and the collection status
        is green; "building" if the field exists but the collection isn't green
        yet; "absent" if the field doesn't exist; "unavailable" if the probe
        itself failed (transport error or non-200).
    """
    base = url.rstrip("/")
    try:
        with _client(timeout) as client:
            resp = client.get(f"{base}/collections/{collection}")
    except httpx.HTTPError:
        return "unavailable"
    if resp.status_code != 200:
        return "unavailable"
    result = resp.json().get("result", {}) or {}
    schema = result.get("payload_schema", {}) or {}
    if "chunk_text" not in schema:
        return "absent"
    return "ready" if result.get("status") == "green" else "building"


def materialize_chunk_text(
    url: str, collection: str, batch: int = 256, timeout: float = 30.0
) -> int:
    """Copy chunk prose into a first-class chunk_text payload field.

    Scrolls points missing ``chunk_text``, extracts prose via
    ``_extract_payload_text``, and writes it back grouped by distinct text
    within each page (fewer ``set_payload`` calls than one per point). Points
    with no recoverable text are skipped and not counted. Mirrors
    ``mcp/qdrant_client.py::QdrantSearchClient.materialize_chunk_text``.

    Args:
        url: Qdrant base URL.
        collection: Collection to materialize into.
        batch: Scroll page size.
        timeout: Request timeout in seconds.

    Returns:
        Number of points written.

    Raises:
        LexicalIndexError: On any non-200 response, or transport failure, from
            scroll or set_payload.
    """
    base = url.rstrip("/")
    written = 0
    offset: Any = None
    with _client(timeout) as client:
        while True:
            body: dict[str, Any] = {
                "limit": batch,
                "with_payload": ["text", "_node_content"],
                "filter": {"must": [{"is_empty": {"key": "chunk_text"}}]},
            }
            if offset is not None:
                body["offset"] = offset
            try:
                resp = client.post(f"{base}/collections/{collection}/points/scroll", json=body)
            except httpx.HTTPError as exc:
                raise LexicalIndexError(f"scroll failed: {exc}") from exc
            if resp.status_code != 200:
                raise LexicalIndexError(f"scroll failed: {resp.status_code}: {resp.text[:200]}")
            result = resp.json().get("result", {}) or {}
            points = result.get("points", []) or []
            by_text: dict[str, list] = {}
            for p in points:
                text = _extract_payload_text(p.get("payload", {}) or {})
                if text:
                    by_text.setdefault(text, []).append(p.get("id"))
            for text, ids in by_text.items():
                try:
                    presp = client.post(
                        f"{base}/collections/{collection}/points/payload",
                        json={"payload": {"chunk_text": text}, "points": ids},
                    )
                except httpx.HTTPError as exc:
                    raise LexicalIndexError(f"set_payload failed: {exc}") from exc
                if presp.status_code != 200:
                    raise LexicalIndexError(
                        f"set_payload failed: {presp.status_code}: {presp.text[:200]}"
                    )
                written += len(ids)
            offset = result.get("next_page_offset")
            if offset is None:
                return written


def create_chunk_text_index(url: str, collection: str, timeout: float = 30.0) -> None:
    """Create the full-text index on chunk_text (idempotent-tolerant).

    Args:
        url: Qdrant base URL.
        collection: Collection to index.
        timeout: Request timeout in seconds.

    Raises:
        LexicalIndexError: On any response other than 200 (created) or 409
            (already exists), or a transport failure.
    """
    base = url.rstrip("/")
    try:
        with _client(timeout) as client:
            resp = client.put(
                f"{base}/collections/{collection}/index",
                json={
                    "field_name": "chunk_text",
                    "field_schema": {"type": "text", "tokenizer": "word", "lowercase": True},
                },
            )
    except httpx.HTTPError as exc:
        raise LexicalIndexError(f"index create failed: {exc}") from exc
    if resp.status_code not in (200, 409):
        raise LexicalIndexError(f"index create failed: {resp.status_code}: {resp.text[:200]}")


def ensure_chunk_text_index(
    url: str, collection: str, batch: int = 256, timeout: float = 30.0
) -> str:
    """Ensure chunk_text exists + is indexed; return the final status.

    Run once after a baseline restore (which silently drops both the field and
    the index) so the first MCP start doesn't pay the materialization cost.
    Degraded states are never raised by this function itself -- only the probe's
    own transport/status failures are folded into "unavailable" (via
    ``chunk_text_status``); a failure of the materialize or index-create step
    propagates, since those are real writes the caller should know failed.

    Args:
        url: Qdrant base URL.
        collection: Collection to ensure.
        batch: Scroll page size passed to ``materialize_chunk_text``.
        timeout: Request timeout in seconds.

    Returns:
        The final ``chunk_text_status()`` value after ensuring.

    Raises:
        LexicalIndexError: On transport-level failure of the materialize or
            index-create steps themselves.
    """
    status = chunk_text_status(url, collection, timeout=timeout)
    if status == "absent":
        materialize_chunk_text(url, collection, batch=batch, timeout=timeout)
        create_chunk_text_index(url, collection, timeout=timeout)
        status = chunk_text_status(url, collection, timeout=timeout)
    return status
