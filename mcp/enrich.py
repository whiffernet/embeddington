"""Bundled vector + KG enrichment tool.

Runs the vector search and the graph lookup as a parallel fan-out, then returns
structured JSON rather than a synthesized text blob — the calling LLM does the
synthesis.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional, Protocol

# Same dual-context import shim as server.py — supports both package import
# (tests, python -m) and direct script invocation (when server.py is run as
# a file path by Claude Desktop, this module gets loaded via sys.path).
try:
    from .arango_client import ArangoError
    from .qdrant_client import QdrantError
except ImportError:
    from arango_client import ArangoError  # type: ignore[no-redef]
    from qdrant_client import QdrantError  # type: ignore[no-redef]

logger = logging.getLogger("embeddington.enrich")

# Multi-word capitalized phrase: "Hardware Asset Management"
_REGEX_CAPITALIZED_SEQ = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")
# CamelCase single token: "IntegrationHub", "ServiceNow" (>=2 humps, no space)
_REGEX_CAMELCASE = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z]*)+)\b")
# Acronym: "CMDB", "ITSM"
_REGEX_ACRONYM = re.compile(r"\b([A-Z]{2,})\b")
# Standalone proper noun: "Discovery", "Server" (single capitalized word, >=3 chars)
_REGEX_PROPER_NOUN = re.compile(r"\b([A-Z][a-z]{2,})\b")

# Capitalized words that start a question/command but are not entities. Without
# these the proper-noun pass (below) would emit "What", "List", etc. as hints.
_STOP_WORDS = {
    "What",
    "How",
    "Tell",
    "Show",
    "Where",
    "Which",
    "Why",
    "When",
    "Who",
    "Whose",
    "Can",
    "Could",
    "Does",
    "Did",
    "Are",
    "Was",
    "Were",
    "Will",
    "Would",
    "Should",
    "List",
    "Find",
    "Give",
    "Explain",
    "Describe",
    "Compare",
    "Define",
    "The",
    "And",
    "For",
    "With",
    "Use",
    "Using",
}


def _extract_entity_hints(query: str) -> list[str]:
    """Cheap regex-based candidate extraction, used only when the caller passes
    ``entity_hints=None``. Claude extracts entities far better than regex, so
    callers SHOULD pass ``entity_hints`` whenever possible (see ``enrich``).

    Catches four shapes, appended in priority order so the most specific
    survive the cap:

      1. multi-word capitalized phrases -> "Hardware Asset Management"
      2. CamelCase tokens               -> "IntegrationHub", "ServiceNow"
      3. acronyms                       -> "CMDB", "ITSM"
      4. standalone proper nouns        -> "Discovery", "Server"

    Proper nouns are lowest priority (most false-positive-prone) and are
    skipped when they are stop words or already part of a captured phrase
    (so "Hardware Asset Management" doesn't also emit a bare "Hardware").

    Args:
        query: The user's natural-language question.

    Returns:
        Deduplicated hint strings, capped to bound KG query fan-out.
    """
    candidates: list[str] = []

    phrases = _REGEX_CAPITALIZED_SEQ.findall(query)
    candidates.extend(phrases)
    candidates.extend(_REGEX_CAMELCASE.findall(query))
    candidates.extend(_REGEX_ACRONYM.findall(query))

    phrase_words = {w for p in phrases for w in p.split()}
    candidates.extend(w for w in _REGEX_PROPER_NOUN.findall(query) if w not in phrase_words)

    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        if c in _STOP_WORDS or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out[:5]  # cap to bound KG fan-out (<= ~20 Arango queries)


# Protocols for dependency injection (lets tests pass mocks)


class _Embed(Protocol):
    async def embed(self, text: str) -> list[float]: ...


class _Qdrant(Protocol):
    async def search(self, vector: list[float], limit: int) -> list[dict]: ...


class _Arango(Protocol):
    def find_entities(self, text: str, limit: int) -> list[dict]: ...

    def neighbors(
        self, entity_id: str, depth: int = 1, types: Optional[list[str]] = None
    ) -> dict: ...


async def enrich(
    query: str,
    entity_hints: Optional[list[str]],
    top_k: int,
    embedding_client: _Embed,
    qdrant_client: _Qdrant,
    arango_client: _Arango,
) -> dict[str, Any]:
    """Run vector search + KG entity-match in parallel, return structured JSON.

    Args:
        query: User's natural-language question.
        entity_hints: Explicit entity names Claude extracted from the query.
            If None, falls back to regex extraction.
        top_k: Number of vector chunks to retrieve.
        embedding_client: Client with async embed() method.
        qdrant_client: Client with async search() method.
        arango_client: Client with sync find_entities() and neighbors() methods.

    Returns:
        dict with keys:
            - vector_chunks: list of {id, score, text, source, metadata} dicts
            - kg_matches: list of {entity, neighbors} dicts
            - errors: dict of {source: message} for any partial failures
    """
    hints = entity_hints if entity_hints is not None else _extract_entity_hints(query)

    # Embedding is required for vector search — if it fails, vector side dies
    # but we still try KG (KG doesn't need embedding).
    vector_task = asyncio.create_task(_vector_side(query, top_k, embedding_client, qdrant_client))
    kg_task = asyncio.create_task(_kg_side(hints, arango_client))

    vector_result, kg_result = await asyncio.gather(vector_task, kg_task)

    errors: dict[str, str] = {}
    if vector_result["error"]:
        errors["qdrant"] = vector_result["error"]
    if kg_result["error"]:
        errors["arango"] = kg_result["error"]

    return {
        "vector_chunks": vector_result["chunks"],
        "kg_matches": kg_result["matches"],
        "errors": errors,
    }


async def _vector_side(
    query: str,
    top_k: int,
    embed: _Embed,
    qdrant: _Qdrant,
) -> dict[str, Any]:
    """Embed query and search Qdrant; returns chunk list and optional error string.

    Args:
        query: Raw query text to embed.
        top_k: Maximum number of chunks to retrieve.
        embed: Embedding client.
        qdrant: Qdrant search client.

    Returns:
        dict with keys chunks (list) and error (str or None).
    """
    try:
        vector = await embed.embed(query)
        chunks = await qdrant.search(vector=vector, limit=top_k)
        return {"chunks": chunks, "error": None}
    except QdrantError as exc:
        logger.warning("vector side failed: %s", exc)
        return {"chunks": [], "error": str(exc)}
    except Exception as exc:
        logger.warning("embedding failed: %s", exc)
        return {"chunks": [], "error": f"embedding: {exc}"}


async def _kg_side(hints: list[str], arango: _Arango) -> dict[str, Any]:
    """Look up each hint in ArangoDB and fetch one-hop neighbors.

    Args:
        hints: Entity name strings to look up.
        arango: ArangoDB KG client.

    Returns:
        dict with keys matches (list of {entity, neighbors}) and error (str or None).
    """
    if not hints:
        return {"matches": [], "error": None}
    try:
        matches: list[dict] = []
        for hint in hints:
            entities = arango.find_entities(hint, limit=3)
            for entity in entities:
                neighbors = arango.neighbors(entity["id"], depth=1)
                matches.append({"entity": entity, "neighbors": neighbors})
        return {"matches": matches, "error": None}
    except ArangoError as exc:
        logger.warning("KG side failed: %s", exc)
        return {"matches": [], "error": str(exc)}
