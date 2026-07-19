"""Bundled vector + KG enrichment tool.

Runs the vector search and the graph lookup as a parallel fan-out, then returns
structured JSON rather than a synthesized text blob — the calling LLM does the
synthesis.
"""

from __future__ import annotations

import asyncio
import logging
import math
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
    from qdrant_client import QdrantError  # type: ignore[no-redef,attr-defined]

try:
    from . import budget as _budget
    from . import hybrid
except ImportError:
    import budget as _budget  # type: ignore[no-redef]
    import hybrid  # type: ignore[no-redef]

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

    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class _Qdrant(Protocol):
    async def search(
        self, vector: list[float], limit: int, match_text: Optional[str] = None
    ) -> list[dict]: ...


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
    edge_budget: int = 40,
    predicates: Optional[list[str]] = None,
    embedding_client: _Embed,
    qdrant_client: _Qdrant,
    arango_client: _Arango,
    max_response_tokens: int = 12000,
    diversity_quota_fraction: float = 0.40,
    score_threshold: float = 0.0,
    lexical_ready: bool = False,
) -> dict[str, Any]:
    """Budgeted parallel vector search + KG concept expansion (spec §3–5).

    The KG half dedups matched entities into concepts, expands every variant,
    and selects edges under a response-level budget with predicate diversity.
    Selection is relevance-injected: quotes from every fetched edge are
    batch-embedded once and cosine-scored against the query vector, then
    ranked ahead of raw confidence (spec §5 PR 3). Any embed failure degrades
    loudly to the legacy confidence-order selection rather than failing the
    whole call. A token-estimate ceiling (server config, not caller-set)
    trims the whole response — vector chunks included — with per-concept
    floors.

    The vector half is hybrid (spec §5 PR 4, issue #38): the dense lane is
    filtered by `score_threshold`, then merged via reciprocal-rank fusion
    with a lexical MatchText lane per identifier-like token in the query
    (only when `lexical_ready`). Qdrant's word tokenizer splits identifiers
    on underscores/punctuation, so each lexical lane is post-filtered down
    to chunks containing the literal token before fusion (see `_vector_side`).
    When identifier tokens are found but the lexical lane is not active, a
    `warnings` entry says so explicitly.

    Args:
        query: User's natural-language question.
        entity_hints: Explicit entity names Claude extracted from the query.
            If None, falls back to regex extraction.
        top_k: Number of vector chunks to retrieve.
        edge_budget: Total KG edge slots to split across matched concepts.
        predicates: Optional predicate allowlist to scope KG expansion.
        embedding_client: Client with async embed() and embed_batch() methods.
        qdrant_client: Client with async search() method.
        arango_client: Client with sync find_entities(), neighbors_stratified(),
            and count_edges() methods.
        max_response_tokens: Token-estimate ceiling for the whole response.
        diversity_quota_fraction: Fraction of each concept's slots reserved
            for the predicate-diversity quota when relevance scoring succeeds
            (server config, wired in via server.py — keeps this module
            config-free like max_response_tokens).
        score_threshold: Minimum dense-lane similarity score a vector chunk
            must clear to survive (server config, wired in via server.py;
            0.0 disables the filter).
        lexical_ready: Whether the lexical MatchText lane may run (server
            config, wired in via server.py from its chunk_text index status).

    Returns:
        {vector_chunks, kg_matches, errors, budget, warnings} — all keys
        always present; see RESPONSE_SHAPES.md.
    """
    warnings: list[str] = []
    hints = entity_hints if entity_hints is not None else _extract_entity_hints(query)
    if not hints:
        warnings.append("no entity hints extracted — pass entity_hints for KG results")

    vector_task = asyncio.create_task(
        _vector_side(
            query,
            top_k,
            embedding_client,
            qdrant_client,
            score_threshold=score_threshold,
            lexical_ready=lexical_ready,
        )
    )
    kg_task = asyncio.create_task(
        asyncio.to_thread(_kg_fetch, hints, arango_client, edge_budget, predicates)
    )
    vector_result, kg_fetched = await asyncio.gather(vector_task, kg_task)

    errors: dict[str, str] = {}
    if vector_result["error"]:
        errors["qdrant"] = vector_result["error"]
    if kg_fetched["error"]:
        errors["arango"] = kg_fetched["error"]
    warnings.extend(kg_fetched["warnings"])

    lexical = vector_result["lexical"]
    if lexical["tokens"] and not lexical["active"]:
        warnings.append("lexical lane degraded — chunk_text index not ready")

    relevance: Optional[dict[str, float]] = None
    quotes: list[str] = []
    quote_to_edges: dict[str, list[str]] = {}
    for item in kg_fetched["prepared"]:
        for eid, ed in item["pool_edges"].items():
            sq = ed.get("source_quote")
            if sq:
                if sq not in quote_to_edges:
                    quotes.append(sq)
                    quote_to_edges[sq] = []
                quote_to_edges[sq].append(eid)
    if quotes:
        try:
            q_vec = vector_result.get("vector")
            if q_vec is None:
                q_vec = await embedding_client.embed(query)
            quote_vecs = await embedding_client.embed_batch(quotes)
            relevance = {}
            for sq, v in zip(quotes, quote_vecs):
                score = _cosine(q_vec, v)
                for eid in quote_to_edges[sq]:
                    relevance[eid] = score
        except Exception as exc:  # noqa: BLE001 — any embed failure degrades, never fails enrich
            logger.warning(
                "relevance scoring failed, degrading to confidence order: %s", exc, exc_info=True
            )
            relevance = None
            warnings.append(
                "relevance scoring unavailable — selection degraded to confidence order"
            )

    kg_result = _kg_select(kg_fetched, relevance, diversity_quota_fraction)

    chunks = vector_result["chunks"]
    # Vector half claims at most ~60% of the ceiling up front (spec §4.1).
    vector_pre_clipped = False
    while len(chunks) > 1 and _budget.estimate_tokens(chunks) > 0.6 * max_response_tokens:
        chunks.pop()
        vector_pre_clipped = True
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
            "truncated": vector_pre_clipped or any(m["truncation"]["truncated"] for m in matches),
        },
        "warnings": warnings,
    }
    return _budget.trim_to_ceiling(result, max_tokens=max_response_tokens)


def _build_suggest(variants: list[dict], pool: list[dict]) -> dict[str, Any]:
    """Drill-down hint for a truncated concept (spec §5.2).

    Args:
        variants: The concept's entity variants (best-ranked first).
        pool: The full edge pool fetched for this concept, used to surface the
            neighborhood's most common predicates worth a follow-up query.

    Returns:
        A suggest dict pointing at kg_neighbors and kg_path follow-ups.
    """
    from collections import Counter

    top_preds = [p for p, _ in Counter(e["predicate"] for e in pool).most_common(3)]
    return {
        "kg_neighbors": {"entity_id": variants[0]["id"], "types": top_preds, "limit": 100},
        "multi_hop": "for dependency chains use kg_path(from_id, to_id)",
    }


async def _vector_side(
    query: str,
    top_k: int,
    embed: _Embed,
    qdrant: _Qdrant,
    *,
    score_threshold: float = 0.0,
    lexical_ready: bool = False,
) -> dict[str, Any]:
    """Embed query, run the hybrid dense+lexical search, and fuse the lanes.

    The dense lane over-fetches (``max(top_k*2, 10)``) and is filtered by
    `score_threshold` (weak chunks dropped, never padded back in — issue
    #38/#47) before fusion. When `lexical_ready`, one lexical MatchText lane
    runs per identifier-like token found in the query (spec §5 PR 4 —
    ``hybrid.extract_identifier_tokens``); Qdrant's word tokenizer indexes
    ``chunk_text`` on subtokens split at underscores/punctuation, so a
    MatchText search for "cmdb_rel_ci" actually matches any chunk containing
    {cmdb, rel, ci} anywhere (all-subtokens-AND, not the literal identifier)
    — each lane over-fetches at ``limit=top_k*2`` and is post-filtered to
    chunks whose text contains the literal token case-insensitively before
    fusion, to give the shrinkage from filtering some headroom. All
    surviving lanes are merged by reciprocal-rank fusion (``hybrid.rrf_merge``)
    and capped to `top_k`. A lexical lane that raises is logged and dropped
    (not propagated) — the fused result still reflects any lanes that did
    succeed.

    Args:
        query: Raw query text to embed.
        top_k: Maximum number of chunks to retrieve.
        embed: Embedding client.
        qdrant: Qdrant search client.
        score_threshold: Minimum dense-lane score to survive (<=0 disables).
        lexical_ready: Whether the lexical MatchText lane may run.

    Returns:
        dict with keys chunks (list), error (str or None), vector (the query
        embedding, reused by relevance scoring in `enrich`; None only when
        the embed call itself failed), and lexical ({tokens, active} —
        active is False whenever lexical_ready was False, no identifier
        tokens were found, or any lexical lane raised).
    """
    tokens = hybrid.extract_identifier_tokens(query)
    try:
        vector = await embed.embed(query)
    except Exception as exc:
        logger.warning("embedding failed: %s", exc)
        return {
            "chunks": [],
            "error": f"embedding: {exc}",
            "vector": None,
            "lexical": {"tokens": tokens, "active": False},
        }

    try:
        dense = await qdrant.search(vector=vector, limit=max(top_k * 2, 10))
    except QdrantError as exc:
        logger.warning("vector side failed: %s", exc)
        return {
            "chunks": [],
            "error": str(exc),
            "vector": vector,
            "lexical": {"tokens": tokens, "active": False},
        }
    dense_thresholded = hybrid.apply_threshold(dense, score_threshold)

    lex_lanes: list[list[dict]] = []
    active = False
    if lexical_ready and tokens:
        active = True
        for tok in tokens:
            try:
                lane = await qdrant.search(vector=vector, limit=top_k * 2, match_text=tok)
                lex_lanes.append([c for c in lane if tok in str(c.get("text", "")).lower()])
            except Exception as exc:  # noqa: BLE001 — a lexical lane degrades, never fails enrich
                logger.warning("lexical lane failed for token %r: %s", tok, exc)
                active = False

    fused = hybrid.rrf_merge([dense_thresholded, *lex_lanes], limit=top_k)
    return {
        "chunks": fused,
        "error": None,
        "vector": vector,
        "lexical": {"tokens": tokens, "active": active},
    }


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (pure python — no numpy
    in the mcp runtime deps).

    Args:
        a: First vector.
        b: Second vector, same length as `a`.

    Returns:
        Cosine similarity in [-1, 1]. Zero vectors are treated as norm 1 to
        avoid a division by zero (yields a score of 0.0, not a crash).
    """
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def _kg_fetch(
    hints: list[str],
    arango: _Arango,
    edge_budget: int,
    predicates: Optional[list[str]],
) -> dict[str, Any]:
    """Seed, group, budget, and fetch pools — everything before selection.

    Sync (runs in a thread). Selection is deferred so the async caller can
    inject query-relevance scores (spec §5 PR 3) computed off-thread.

    Args:
        hints: Entity name strings to look up.
        arango: ArangoDB KG client.
        edge_budget: Total edge slots to split across matched concepts.
        predicates: Optional predicate allowlist to scope KG expansion.

    Returns:
        dict with keys prepared (list of {match, pool_nodes, pool_edges,
        n_slots}), error (str or None), warnings (list of str).
    """
    if not hints:
        return {"prepared": [], "error": None, "warnings": []}
    try:
        seeded: list[tuple[int, dict]] = []
        for i, hint in enumerate(hints):
            for entity in arango.find_entities(hint, limit=3):
                seeded.append((i, entity))
    except ArangoError as exc:
        logger.warning("KG side failed: %s", exc)
        return {"prepared": [], "error": str(exc), "warnings": []}

    concepts = _budget.group_concepts(seeded)
    slots = _budget.allocate_budget(concepts, edge_budget)
    prepared: list[dict] = []
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
        pool_nodes: dict[str, dict] = {}
        pool_edges: dict[str, dict] = {}
        eff_slots = n_slots
        try:
            if predicates:
                available = sum(arango.count_edges(v["id"], predicates) for v in concept.variants)
            else:
                available = sum(int(v.get("degree") or 0) for v in concept.variants)
            match["truncation"]["available"] = available
            if n_slots > 0:
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
        except ArangoError as exc:
            logger.warning("concept %s failed: %s", concept.key, exc)
            match["error"] = str(exc)
            pool_nodes, pool_edges, eff_slots = {}, {}, 0
        prepared.append(
            {
                "match": match,
                "pool_nodes": pool_nodes,
                "pool_edges": pool_edges,
                "n_slots": eff_slots,
            }
        )
    return {"prepared": prepared, "error": None, "warnings": []}


def _kg_select(
    fetched: dict[str, Any],
    relevance: Optional[dict[str, float]],
    diversity_quota_fraction: float,
) -> dict[str, Any]:
    """Pure selection + match assembly over pre-fetched pools (spec §5 PR 3).

    Args:
        fetched: `_kg_fetch` output.
        relevance: Injected relevance scores keyed by str(edge id); None
            degrades to the legacy confidence/diversity selection.
        diversity_quota_fraction: Fraction of each concept's slots reserved
            for the predicate-diversity quota when relevance is present.

    Returns:
        dict with keys matches, error, warnings (the historical `_kg_side`
        shape).
    """
    matches: list[dict] = []
    for item in fetched["prepared"]:
        match = item["match"]
        n_slots = item["n_slots"]
        pool = list(item["pool_edges"].values())
        if match["error"] is not None:
            matches.append(match)
            continue
        if n_slots > 0:
            quota = (
                max(1, round(diversity_quota_fraction * n_slots)) if relevance is not None else None
            )
            selected = _budget.select_edges(
                pool, n_slots, relevance=relevance, diversity_quota=quota
            )
            keep_ids = {e["source"] for e in selected} | {e["target"] for e in selected}
            match["edges"] = selected
            match["nodes"] = [n for n in item["pool_nodes"].values() if n["id"] in keep_ids]
            match["truncation"]["returned"] = len(selected)
            # Compare against the actual fetched pool, not `available` (a
            # degree-derived estimate) — spec §5.3: never compare
            # mismatched counting bases.
            match["truncation"]["truncated"] = len(pool) > len(selected)
            if match["truncation"]["truncated"]:
                match["suggest"] = _build_suggest(match["variants"], pool)
        else:
            match["truncation"]["truncated"] = match["truncation"]["available"] > 0
            if match["truncation"]["truncated"]:
                match["suggest"] = _build_suggest(match["variants"], [])
        matches.append(match)
    return {"matches": matches, "error": fetched["error"], "warnings": fetched["warnings"]}


def _kg_side(
    hints: list[str],
    arango: _Arango,
    edge_budget: int,
    predicates: Optional[list[str]],
) -> dict[str, Any]:
    """Legacy composition: fetch then select with no relevance (spec §6 path).

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
    return _kg_select(
        _kg_fetch(hints, arango, edge_budget, predicates),
        relevance=None,
        diversity_quota_fraction=_budget.DIVERSITY_QUOTA_FRACTION,
    )
