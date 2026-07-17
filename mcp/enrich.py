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

try:
    from . import budget as _budget
except ImportError:
    import budget as _budget  # type: ignore[no-redef]

logger = logging.getLogger("embeddington.enrich")

# Multi-word capitalized phrase: "Hardware Asset Management"
_REGEX_CAPITALIZED_SEQ = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")
# CamelCase single token: "IntegrationHub", "ServiceNow" (>=2 humps, no space)
_REGEX_CAMELCASE = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z]*)+)\b")
# Acronym: "CMDB", "ITSM"
_REGEX_ACRONYM = re.compile(r"\b([A-Z]{2,})\b")
# Standalone proper noun: "Discovery", "Server" (single capitalized word, >=3 chars)
_REGEX_PROPER_NOUN = re.compile(r"\b([A-Z][a-z]{2,})\b")
# Lowercase snake_case identifier: "cmdb_rel_ci", "sys_user" — ServiceNow
# table/field names are overwhelmingly lowercase; every other pattern here
# requires a leading capital and misses them entirely.
_REGEX_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")

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

    Catches five shapes, appended in priority order so the most specific
    survive the cap:

      1. multi-word capitalized phrases -> "Hardware Asset Management"
      2. CamelCase tokens               -> "IntegrationHub", "ServiceNow"
      3. acronyms                       -> "CMDB", "ITSM"
      4. snake_case identifiers         -> "cmdb_rel_ci", "sys_user"
      5. standalone proper nouns        -> "Discovery", "Server"

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
    candidates.extend(_REGEX_SNAKE.findall(query))

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
    def find_entities(self, text: str, limit: int = 10) -> list[dict]: ...

    def neighbors_stratified(
        self,
        entity_id: str,
        per_predicate: int = 2,
        overall: int = 50,
        predicates: Optional[list[str]] = None,
    ) -> dict: ...

    def count_edges(self, entity_id: str, predicates: Optional[list[str]] = None) -> int: ...


async def enrich(
    query: str,
    entity_hints: Optional[list[str]],
    top_k: int,
    *,
    edge_budget: int = 60,
    predicates: Optional[list[str]] = None,
    embedding_client: _Embed,
    qdrant_client: _Qdrant,
    arango_client: _Arango,
    max_response_tokens: int = 12000,
) -> dict[str, Any]:
    """Budgeted parallel vector search + KG concept expansion (spec §3–4).

    The KG half dedups matched entities into concepts, expands every variant,
    and selects edges under a response-level budget with predicate diversity.
    A token-estimate ceiling (server config, not caller-set) trims the whole
    response — vector chunks included — with per-concept floors.

    Args:
        query: User's natural-language question.
        entity_hints: Explicit entity names Claude extracted from the query.
            If None, falls back to regex extraction.
        top_k: Number of vector chunks to retrieve.
        edge_budget: Total KG edge slots to split across matched concepts.
        predicates: Optional predicate allowlist to scope KG expansion.
        embedding_client: Client with async embed() method.
        qdrant_client: Client with async search() method.
        arango_client: Client with sync find_entities(), neighbors_stratified(),
            and count_edges() methods.
        max_response_tokens: Token-estimate ceiling for the whole response.

    Returns:
        {vector_chunks, kg_matches, errors, budget, warnings} — all keys
        always present; see RESPONSE_SHAPES.md.
    """
    warnings: list[str] = []
    hints = entity_hints if entity_hints is not None else _extract_entity_hints(query)
    if not hints:
        warnings.append("no entity hints extracted — pass entity_hints for KG results")

    vector_task = asyncio.create_task(_vector_side(query, top_k, embedding_client, qdrant_client))
    kg_task = asyncio.create_task(
        asyncio.to_thread(_kg_side, hints, arango_client, edge_budget, predicates)
    )
    vector_result, kg_result = await asyncio.gather(vector_task, kg_task)

    errors: dict[str, str] = {}
    if vector_result["error"]:
        errors["qdrant"] = vector_result["error"]
    if kg_result["error"]:
        errors["arango"] = kg_result["error"]
    warnings.extend(kg_result["warnings"])

    chunks = vector_result["chunks"]
    # Vector half claims at most ~60% of the ceiling up front (spec §4.1).
    while len(chunks) > 1 and _budget.estimate_tokens(chunks) > 0.6 * max_response_tokens:
        chunks.pop()
        if "response ceiling: vector chunks trimmed" not in warnings:
            warnings.append("response ceiling: vector chunks trimmed")

    matches = kg_result["matches"]
    result = {
        "vector_chunks": chunks,
        "kg_matches": matches,
        "errors": errors,
        "budget": {
            "edge_budget": edge_budget,
            "returned": sum(len(m["edges"]) for m in matches),
            "truncated": any(m["truncation"]["truncated"] for m in matches),
        },
        "warnings": warnings,
    }
    return _budget.trim_to_ceiling(result, max_tokens=max_response_tokens)


def _build_suggest(variants: list[dict], dropped_pool: list[dict]) -> dict[str, Any]:
    """Drill-down hint for a truncated concept (spec §5.2).

    Args:
        variants: The concept's entity variants (best-ranked first).
        dropped_pool: The full edge pool fetched for this concept, used to
            surface the most common predicates worth a follow-up query.

    Returns:
        A suggest dict pointing at kg_neighbors and kg_path follow-ups.
    """
    from collections import Counter

    top_preds = [p for p, _ in Counter(e["predicate"] for e in dropped_pool).most_common(3)]
    return {
        "kg_neighbors": {"entity_id": variants[0]["id"], "types": top_preds, "limit": 100},
        "multi_hop": "for dependency chains use kg_path(from_id, to_id)",
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


def _kg_side(
    hints: list[str],
    arango: _Arango,
    edge_budget: int,
    predicates: Optional[list[str]],
) -> dict[str, Any]:
    """Concept-grouped, budget-allocated KG expansion. Sync (runs in a thread).

    Per-concept errors scope to that match; the top-level error is reserved
    for total failure (e.g. find_entities itself unreachable).

    Args:
        hints: Entity name strings to look up.
        arango: ArangoDB KG client.
        edge_budget: Total edge slots to split across matched concepts.
        predicates: Optional predicate allowlist to scope KG expansion.

    Returns:
        dict with keys matches (list of match dicts), error (str or None),
        and warnings (list of str).
    """
    if not hints:
        return {"matches": [], "error": None, "warnings": []}
    try:
        seeded: list[tuple[int, dict]] = []
        for i, hint in enumerate(hints):
            for entity in arango.find_entities(hint, limit=3):
                seeded.append((i, entity))
    except ArangoError as exc:
        logger.warning("KG side failed: %s", exc)
        return {"matches": [], "error": str(exc), "warnings": []}

    concepts = _budget.group_concepts(seeded)
    slots = _budget.allocate_budget(concepts, edge_budget)
    warnings: list[str] = []
    matches: list[dict] = []

    for concept, n_slots in zip(concepts, slots + [0] * (len(concepts) - len(slots))):
        match: dict[str, Any] = {
            "concept": concept.key,
            "variants": concept.variants,
            "nodes": [],
            "edges": [],
            "truncation": {"truncated": False, "available": 0, "returned": 0},
            "suggest": None,
            "error": None,
        }
        try:
            if predicates:
                available = sum(arango.count_edges(v["id"], predicates) for v in concept.variants)
            else:
                available = sum(int(v.get("degree") or 0) for v in concept.variants)
            match["truncation"]["available"] = available
            if n_slots > 0:
                pool_nodes: dict[str, dict] = {}
                pool_edges: dict[str, dict] = {}
                for v in concept.variants:
                    fetched = arango.neighbors_stratified(
                        v["id"],
                        per_predicate=2,
                        overall=max(2 * n_slots, 20),
                        predicates=predicates,
                    )
                    for nd in fetched["nodes"]:
                        pool_nodes.setdefault(nd["id"], nd)
                    for ed in fetched["edges"]:
                        pool_edges.setdefault(str(ed["id"]), ed)
                pool = list(pool_edges.values())
                selected = _budget.select_edges(pool, n_slots)
                keep_ids = {e["source"] for e in selected} | {e["target"] for e in selected}
                match["edges"] = selected
                match["nodes"] = [n for n in pool_nodes.values() if n["id"] in keep_ids]
                match["truncation"]["returned"] = len(selected)
                # Compare against the actual fetched pool, not `available` (a
                # degree-derived estimate) — spec §5.3: never compare
                # mismatched counting bases.
                match["truncation"]["truncated"] = len(pool) > len(selected)
                if match["truncation"]["truncated"]:
                    match["suggest"] = _build_suggest(concept.variants, pool)
            else:
                match["truncation"]["truncated"] = available > 0
                if match["truncation"]["truncated"]:
                    match["suggest"] = _build_suggest(concept.variants, [])
        except ArangoError as exc:
            logger.warning("concept %s failed: %s", concept.key, exc)
            match["error"] = str(exc)
        matches.append(match)

    return {"matches": matches, "error": None, "warnings": warnings}
