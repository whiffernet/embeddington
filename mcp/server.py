"""embeddington MCP server — direct RAG + KG access for Claude Desktop.

Exposes 7 tools. Arango access is via a scoped read-only user; Qdrant access
is via a code-scoped client (no JWT in v1 — see spec §5). Returns structured
JSON for the caller's LLM (Claude) to synthesize; no external LLM in the loop.

Runs as a stdio subprocess of Claude Desktop. Configuration is loaded from
a `.env` file alongside this script — see .env.example for the shape and
README.md for setup. Pre-existing process env vars (e.g. injected by
claude_desktop_config.json) take precedence and are NOT overridden, so
the same code works for both setups.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Annotated, Any, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastmcp import FastMCP
from pydantic import Field

# Load .env BEFORE importing config — config reads env vars at module level.
# override=False so values already set in the process env (e.g. by Claude
# Desktop's claude_desktop_config.json) win over the .env file. This means
# the same code supports both deployment patterns.
_ENV_PATH = Path(__file__).resolve().parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)

# Imports work in two contexts:
#   1. As a package (tests, `python -m server`) — relative imports
#   2. As a direct script (Claude Desktop calling `python3 .../server.py`) — script-style
# We support both for test ergonomics.
try:
    from . import config
    from .arango_client import ArangoError, ArangoKGClient
    from .embedding_client import EmbeddingClient
    from .enrich import _vector_side as _hybrid_vector_side
    from .enrich import enrich as _enrich_impl
    from .qdrant_client import QdrantSearchClient
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import config  # type: ignore[no-redef]
    from arango_client import ArangoError, ArangoKGClient  # type: ignore[no-redef]
    from embedding_client import EmbeddingClient  # type: ignore[no-redef]
    from enrich import _vector_side as _hybrid_vector_side  # type: ignore[no-redef]
    from enrich import enrich as _enrich_impl  # type: ignore[no-redef]
    from qdrant_client import QdrantSearchClient  # type: ignore[no-redef,attr-defined]

# --- Logging — stderr only (stdout reserved for MCP stdio) ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp.embeddington")

# --- Lazy client init -----------------------------------------------------
_embed_clients: dict[str, EmbeddingClient] = {}
_qdrant_clients: dict[str, QdrantSearchClient] = {}
_arango: ArangoKGClient | None = None

# --- Lexical (chunk_text) index status --------------------------------------
# Set at every server start by _isolation_sanity_check(); "ready" is the only
# state that permits the lexical MatchText lane (spec §5 PR 4, issue #38).
# The chunk_text field and its index are built exclusively by the consumer
# install/update flow (never this server — this server issues zero Qdrant
# writes); a lazy re-probe (_maybe_reprobe, below) notices when that flow
# has since brought the lane to "ready", without a server restart.
_lexical_status: str = "absent"
_LEXICAL_REENSURE_INTERVAL = 60.0
# -inf, not 0.0: time.monotonic()'s reference point is unspecified (often
# system boot, not epoch) — on a host booted <60s ago, 0.0 can be LESS than
# 60s behind the first real `now`, which would make the throttle guard below
# wrongly treat that first call as still-within-window and skip it too.
_lexical_last_reensure: float = float("-inf")
# True while a re-probe is in flight. A read-only status probe is sub-second,
# so overlap is not the correctness concern it was for the old write-based
# self-heal (a multi-minute materialize) — this flag is kept for
# call-coalescing symmetry with that prior shape, so concurrent tool calls
# within the same window share one probe instead of each firing their own.
# It is checked-and-set before the first `await` in _maybe_reprobe, so no
# asyncio.Lock is needed for that atomicity.
_lexical_reensure_in_flight: bool = False


def _get_embed(index: str | None = None) -> EmbeddingClient:
    """Return (or create) the cached EmbeddingClient for an /embed index.

    Args:
        index: The per-collection embed index (e.g. "technology" -> bge-m3).
            Defaults to config.DEFAULT_EMBED_INDEX.

    Returns:
        A cached EmbeddingClient pinned to that index.
    """
    index = index or config.DEFAULT_EMBED_INDEX
    if index not in _embed_clients:
        _embed_clients[index] = EmbeddingClient(
            url=config.EMBED_URL,
            index=index,
            timeout=config.HTTP_TIMEOUT,
        )
    return _embed_clients[index]


def _get_qdrant(collection: str | None = None) -> QdrantSearchClient:
    """Return (or create) the cached QdrantSearchClient for a collection.

    Each client is pinned to one collection at construction and never
    overridden by a caller. Callers MUST validate `collection` against
    config.ALLOWED_QDRANT_COLLECTIONS before passing it here — this getter
    does not validate (see vector_search for the boundary guard).

    Args:
        collection: Qdrant collection name. Defaults to
            config.DEFAULT_QDRANT_COLLECTION.

    Returns:
        A cached QdrantSearchClient pinned to that collection.
    """
    collection = collection or config.DEFAULT_QDRANT_COLLECTION
    if collection not in _qdrant_clients:
        _qdrant_clients[collection] = QdrantSearchClient(
            url=config.QDRANT_URL,
            collection=collection,
            timeout=config.HTTP_TIMEOUT,
        )
    return _qdrant_clients[collection]


def _get_arango() -> ArangoKGClient:
    """Return (or create) the singleton ArangoKGClient.

    Returns:
        The module-level ArangoKGClient instance.
    """
    global _arango
    if _arango is None:
        _arango = ArangoKGClient(
            url=config.ARANGO_URL,
            database=config.ARANGO_DATABASE,
            username=config.ARANGO_USER,
            password=config.ARANGO_PASSWORD,
        )
    return _arango


# --- Predicate cache --------------------------------------------------------
_schema_predicates: set[str] | None = None


def _get_known_predicates() -> set[str] | None:
    """Cached UPPER-cased predicate list from kg_schema; None = unavailable.

    Populated lazily on first use and cached for the process lifetime — the
    KG schema doesn't change within a running server. On Arango failure the
    cache is left unset (not poisoned with an empty set) so a transient
    outage doesn't permanently disable predicate validation; the caller
    treats None as "skip validation" rather than "no predicates exist".

    Returns:
        Set of upper-cased predicate strings, or None if the schema could
        not be fetched.
    """
    global _schema_predicates
    if _schema_predicates is None:
        try:
            _schema_predicates = {
                str(p).upper() for p in _get_arango().schema().get("predicates", [])
            }
        except ArangoError:
            return None
    return _schema_predicates


# --- Startup sanity check -------------------------------------------------


async def _isolation_sanity_check() -> None:
    """Verify the MCP's runtime configuration is safe to expose tools.

    Checks before exposing tools:
      - POSITIVE: the configured Qdrant URL can serve all allowlisted Qdrant
        collections. If not, we'd return empty results forever.

    No Qdrant deny check in v1: there's no credential enforcement at the
    Qdrant layer (see spec §5). The future JWT-enabled version adds that.

    Also probes the lexical (chunk_text) index status and records it in the
    module-level `_lexical_status` (spec §5 PR 4, issue #38). This is a
    read-only probe — the index itself is built by the consumer
    install/update flow, never by this server. A failed probe is logged and
    leaves the status "unavailable" — it must never block startup, since the
    lexical lane is an enhancement, not a dependency of the dense lane.

    Also runs two warn-only probes so a misconfigured BYO-prod store fails
    LOUD instead of booting clean and silently returning empty/degraded
    results: an Arango probe (one cheap allowlisted read — catches a wrong
    ARANGO_DATABASE or a missing grant) and an embed probe (one embed call —
    the client already raises on both unreachable and wrong-dims, so the
    exception path alone is the signal). Neither ever raises out of this
    function; both only log.
    """
    global _lexical_status

    qdrant = _get_qdrant()

    leaks: list[str] = []

    for collection in config.ALLOWED_QDRANT_COLLECTIONS:
        if not await qdrant.can_read_collection(collection):
            leaks.append(
                f"Qdrant collection '{collection}' is unreachable "
                f"(check QDRANT_URL and that the collection exists)"
            )

    if leaks:
        msg = "Refusing to start:\n  " + "\n  ".join(leaks)
        logger.error(msg)
        raise SystemExit(msg)

    logger.info(
        "Sanity check passed: Qdrant collections %s reachable",
        sorted(config.ALLOWED_QDRANT_COLLECTIONS),
    )

    try:
        _lexical_status = await qdrant.chunk_text_status()
        logger.info("Lexical (chunk_text) index status: %s", _lexical_status)
        if _lexical_status != "ready":
            logger.warning(
                "lexical lane degraded (chunk_text %s) — the index is built by the "
                "install/update flow, never by this server; dense search is unaffected",
                _lexical_status,
            )
    except Exception as exc:  # noqa: BLE001 — status probe must never block startup
        logger.warning("chunk_text status probe failed at startup: %s", exc)
        _lexical_status = "unavailable"

    try:
        _get_arango().probe_read()
        logger.info(
            "Arango probe passed (db=%s user=%s)", config.ARANGO_DATABASE, config.ARANGO_USER
        )
    except Exception as exc:  # noqa: BLE001 — probe must never block startup
        logger.warning(
            "Arango probe FAILED (db=%s user=%s): %s — KG tools will return empty "
            "results until fixed (check ARANGO_DATABASE / ARANGO_USER grants)",
            config.ARANGO_DATABASE,
            config.ARANGO_USER,
            exc,
        )

    try:
        await _get_embed().embed("startup probe")  # raises on unreachable OR wrong dims
        logger.info("Embed probe passed")
    except Exception as exc:  # noqa: BLE001 — probe must never block startup
        logger.warning(
            "Embed probe FAILED: %s — vector search will fail until EMBED_URL is reachable/correct",
            exc,
        )


async def _maybe_reprobe() -> None:
    """Refresh the lexical status READ-ONLY, at most once per 60s.

    The install/update flow (never this server) builds the index; a server that
    started degraded must notice — without a restart — once the flow builds it.
    Replaces the retired write-path self-heal: probes ``chunk_text_status`` and
    mutates nothing.
    """
    global _lexical_status, _lexical_last_reensure, _lexical_reensure_in_flight
    if _lexical_reensure_in_flight:
        return
    now = time.monotonic()
    if now - _lexical_last_reensure < _LEXICAL_REENSURE_INTERVAL:
        return
    _lexical_last_reensure = now
    _lexical_reensure_in_flight = True
    try:
        _lexical_status = await _get_qdrant().chunk_text_status()
    except Exception as exc:  # noqa: BLE001 — a failed probe must not fail the tool call
        logger.warning("lexical status re-probe failed: %s", exc)
    finally:
        _lexical_reensure_in_flight = False


# --- MCP server + tools ---------------------------------------------------

mcp = FastMCP("embeddington")


@mcp.tool
async def enrich(
    query: Annotated[str, Field(description="The user's natural-language question.")],
    entity_hints: Annotated[
        Optional[list[str]],
        Field(
            description="Entity names you (Claude) extracted from the query — e.g. "
            "['Workflow Studio', 'Process Automation Designer']. "
            "Pass these whenever possible; falls back to regex if None."
        ),
    ] = None,
    top_k: Annotated[
        int, Field(ge=1, le=50, description="Max vector chunks to return (may return fewer).")
    ] = 5,
    edge_budget: Annotated[
        int,
        Field(
            ge=1,
            le=200,
            description="TOTAL KG edges across the whole response, allocated "
            "across matched concepts. Truncation is explicit — see each "
            "match's truncation object and suggest hint. Relevance-aware "
            "selection makes a larger budget productive up to ~60 (measured "
            "gold-recall 0.186->0.281 as edge_budget went 20->60); past that "
            "point the response ceiling increasingly trims the larger "
            "allocation, REDUCING relevance (0.281->0.225 by edge_budget=120). "
            "The default of 60 already sits at that peak, so raising it adds "
            "latency without adding relevant edges — prefer lowering top_k "
            "for stronger KG grounding instead.",
        ),
    ] = 60,
    predicates: Annotated[
        Optional[list[str]],
        Field(
            description="Relationship predicate filter (case-insensitive). "
            "Leave null unless you have already called kg_schema — guessed "
            "names are validated and flagged in warnings."
        ),
    ] = None,
) -> dict[str, Any]:
    """Default starting tool: budgeted parallel vector search + KG concept match.

    Always uses the default `technology` collection for its vector half and
    the shared ServiceNow KG for its entity half.

    Returns structured JSON ({vector_chunks, kg_matches, errors, budget,
    warnings, grounding}) — no synthesis. Claude does all reasoning over the
    returned data.

    `grounding` labels what the response actually contains, classified after
    all trimming: `tier` is "ok" (retrieval landed and, when the query named
    an identifier, that identifier appears in the returned content), "weak"
    (something came back but is thin — e.g. the asked-for identifier is
    missing from every chunk and edge quote), or "none" (nothing came back
    at all); `reasons` explains why whenever `tier` is not "ok". On
    grounding.tier "none" or "weak", say what was not found rather than
    answering from prior knowledge — never present an identifier that is
    not in the returned content.

    Grounding: each `kg_matches[].variants[0]` carries `source_documents` +
    `releases` (+ `degree`); each `kg_matches[].edges[]` carries
    `source_quote` (verbatim, citable), `confidence`, `extraction_type`, and
    `releases`. Cite the `source_quote` for any relationship you use, treat
    inferred/low-confidence edges as tentative, and scope version-sensitive
    claims to `releases`.

    Responses are budget-bounded: kg_matches groups entity variants into
    concepts (variants[0] = best-ranked); each match's `truncation` reports
    {truncated, available, returned}; when truncated, `suggest` gives the
    kg_neighbors/kg_path drill-down. A server-side token ceiling keeps the
    response within the client cap (or flags it loudly in `warnings` in the
    rare case per-concept floors force a small overflow). Relevance-aware
    selection (PR 3) makes a larger edge_budget productive up to ~60
    (measured: mean gold-recall@budget 0.186 -> 0.268 -> 0.281 as
    edge_budget went 20 -> 40 -> 60 at top_k=5) — the default. Past ~60 it
    REDUCES relevance under the response ceiling (0.281 -> 0.248 -> 0.225 at
    80 / 120) rather than merely plateauing: a larger allocation increasingly
    competes with itself for the same token space (see
    mcp/tests/gold/PR6-EVIDENCE.md). For stronger KG grounding prefer
    LOWERING top_k rather than raising edge_budget past the default.

    The vector half (`vector_chunks`) is hybrid: a dense lane is filtered by
    the server-configured score threshold and, when the lexical chunk_text
    index is ready, fused via reciprocal-rank fusion with a lexical
    MatchText lane per identifier-like token found in the query. Weak
    matches are dropped rather than padded back in, so `vector_chunks` MAY
    number fewer than `top_k`.

    Args:
        query: The user's natural-language question.
        entity_hints: Entity names pre-extracted by Claude from the query.
        top_k: Maximum number of vector chunks to return (1-50); the actual
            count may be lower once the score threshold and dedup are
            applied.
        edge_budget: Total KG edge slots to split across matched concepts
            (1-200, default 60).
        predicates: Optional relationship predicate filter. Unknown
            predicates (per kg_schema) are flagged in warnings, not rejected.

    Returns:
        dict with keys vector_chunks, kg_matches, errors, budget, warnings,
        grounding.
    """
    if _lexical_status != "ready":
        await _maybe_reprobe()

    server_warnings: list[str] = []
    norm_predicates: Optional[list[str]] = None
    if predicates:
        norm_predicates = [p.upper() for p in predicates]
        known = _get_known_predicates()
        if known is not None:
            unknown = [p for p in norm_predicates if p not in known]
            if unknown:
                server_warnings.append(
                    f"unknown predicates (call kg_schema): {sorted(p.lower() for p in unknown)}"
                )
    result = await _enrich_impl(
        query=query,
        entity_hints=entity_hints,
        top_k=top_k,
        edge_budget=edge_budget,
        predicates=norm_predicates,
        embedding_client=_get_embed(),
        qdrant_client=_get_qdrant(),
        arango_client=_get_arango(),
        max_response_tokens=config.MAX_RESPONSE_TOKENS,
        diversity_quota_fraction=config.DIVERSITY_QUOTA_FRACTION,
        score_threshold=config.SCORE_THRESHOLD,
        lexical_ready=(_lexical_status == "ready"),
    )
    result["warnings"] = server_warnings + result["warnings"]
    if _lexical_status != "ready" and not any(
        "lexical lane degraded" in w for w in result["warnings"]
    ):
        result["warnings"].append("lexical lane degraded — chunk_text index not ready")
    return result


@mcp.tool
async def vector_search(
    query: Annotated[str, Field(description="Search query.")],
    collection: Annotated[
        Optional[str],
        Field(
            description="Which Qdrant collection to search. "
            "Defaults to 'technology' (the ServiceNow MD corpus). "
            "Only allowlisted collections are accepted; unknown names return an error."
        ),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> dict[str, Any]:
    """Raw vector search against an allowlisted Qdrant collection.

    The query is embedded with the encoder matching the target collection.
    Unknown collections are rejected before any client is constructed — the
    allowlist is the only Qdrant scope guard in v1 (see spec §5).

    Hybrid retrieval (spec §5 PR 4, issue #38): the dense lane is filtered by
    the server-configured score threshold and, when the lexical chunk_text
    index is ready, fused via reciprocal-rank fusion with a lexical
    MatchText lane per identifier-like token found in the query. Weak
    matches are dropped rather than padded back in, so this call MAY return
    fewer than `limit` results.

    Args:
        query: Natural-language search query to embed and search.
        collection: Allowlisted collection name; defaults to technology (m3).
        limit: Maximum number of results to return (1-50); the actual count
            may be lower once the score threshold and dedup are applied.

    Returns:
        dict with keys results, count, collection, warnings, and optional
        error. `warnings` carries the exact string "lexical lane degraded —
        chunk_text index not ready" whenever the chunk_text index isn't
        ready, on every return path (success, error, and unknown
        collection); empty list otherwise.
    """
    collection = collection or config.DEFAULT_QDRANT_COLLECTION
    if collection not in config.ALLOWED_QDRANT_COLLECTIONS:
        server_warnings: list[str] = []
        if _lexical_status != "ready":
            server_warnings.append("lexical lane degraded — chunk_text index not ready")
        return {
            "results": [],
            "count": 0,
            "collection": collection,
            "error": f"unknown collection '{collection}'; allowed: "
            f"{sorted(config.ALLOWED_QDRANT_COLLECTIONS)}",
            "warnings": server_warnings,
        }
    if _lexical_status != "ready":
        await _maybe_reprobe()
    server_warnings = []
    if _lexical_status != "ready":
        server_warnings.append("lexical lane degraded — chunk_text index not ready")
    index = config.ALLOWED_QDRANT_COLLECTIONS[collection]
    result = await _hybrid_vector_side(
        query,
        limit,
        _get_embed(index),
        _get_qdrant(collection),
        score_threshold=config.SCORE_THRESHOLD,
        lexical_ready=(_lexical_status == "ready"),
    )
    if result["error"]:
        return {
            "results": [],
            "count": 0,
            "collection": collection,
            "error": result["error"],
            "warnings": server_warnings,
        }
    return {
        "results": result["chunks"],
        "count": len(result["chunks"]),
        "collection": collection,
        "warnings": server_warnings,
    }


@mcp.tool
async def kg_find_entities(
    text: Annotated[str, Field(description="Text to fuzzy-match against entity names.")],
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> dict[str, Any]:
    """Find KG entities whose name contains `text`.

    Args:
        text: Text to fuzzy-match (case-insensitive) against entity names.
        limit: Maximum number of entities to return (1-50).

    Returns:
        dict with keys entities (list), count, and optional error. Each entity
        is `{id, name, type, source_documents, releases}` — source_documents
        (first 5) for citation, releases for version context.
    """
    try:
        entities = _get_arango().find_entities(text=text, limit=limit)
        return {"entities": entities, "count": len(entities)}
    except ArangoError as exc:
        return {"entities": [], "count": 0, "error": str(exc)}


@mcp.tool
async def kg_get_entity(
    entity_id: Annotated[str, Field(description="Full document ID, e.g. 'entities_v2/abc'.")],
) -> dict[str, Any]:
    """Fetch a full entity document by its _id.

    Args:
        entity_id: Full ArangoDB document ID (collection/key format).

    Returns:
        dict with key entity (doc or None) and optional error.
    """
    try:
        entity = _get_arango().get_entity(entity_id)
        if entity is None:
            return {"entity": None, "error": "entity not found"}
        return {"entity": entity}
    except ArangoError as exc:
        return {"entity": None, "error": str(exc)}


@mcp.tool
async def kg_neighbors(
    entity_id: Annotated[str, Field(description="Full document ID.")],
    depth: Annotated[int, Field(ge=1, le=3, description="Traversal depth (capped at 3).")] = 1,
    types: Annotated[
        Optional[list[str]],
        Field(
            description="Optional list of relationship predicates to filter by. "
            "Call kg_schema first to discover what predicates exist."
        ),
    ] = None,
    limit: Annotated[
        int,
        Field(
            ge=1,
            le=500,
            description="Cap on raw traversal rows (pre-dedup). Default 100 "
            "keeps responses under Claude Code's tool-result cap. Raise "
            "explicitly for broad exploration.",
        ),
    ] = 100,
) -> dict[str, Any]:
    """Return connected entities + edges around `entity_id`.

    Depth=2 traversal of high-degree hub entities can produce 80KB+ of
    JSON, which Claude Code dumps to a file-fallback path and pushes the
    consumer into jq-extraction mode. Default `limit=100` keeps the
    response readable inline; narrow further with `types` for predicate
    filtering.

    Grounding: each edge carries `source_quote` (verbatim, citable),
    `source_document`, `confidence`, `extraction_type` ("explicit"/"inferred"),
    and `releases`. When you use a relationship in an answer, cite its
    `source_quote`, treat inferred or low-confidence edges as tentative, and
    scope version-sensitive claims to the edge's `releases`.

    `truncation` reports whether `limit` cut off the raw traversal
    (`truncated`), the true depth-1 edge count when `types` was given
    (`available` — an extra count_edges query, which only counts immediate
    neighbors; it is populated only for depth-1 predicate-filtered calls,
    where count_edges gives an exact basis, and is otherwise null — both
    when `types` is omitted and when `depth` > 1, since a depth-1 count
    isn't a meaningful ceiling for a multi-hop `returned`), and how many
    edges this call actually returned (`returned`).

    Args:
        entity_id: Full ArangoDB document ID to traverse from.
        depth: Traversal depth, 1-3.
        types: Optional relationship predicate filter list.
        limit: Max raw traversal rows (1-500, default 100).

    Returns:
        dict with keys nodes, edges, truncation, and optional error. Node
        dicts carry `releases` for per-entity version context.
    """
    try:
        result = _get_arango().neighbors(entity_id, depth=depth, types=types, limit=limit)
    except ArangoError as exc:
        return {
            "nodes": [],
            "edges": [],
            "truncation": {"truncated": False, "available": None, "returned": 0},
            "error": str(exc),
        }
    fetched = result.pop("fetched", 0)
    available = None
    if types and depth == 1:
        # Best-effort: count_edges is a secondary enrichment query — its
        # failure must not discard the neighbors() payload we already have.
        try:
            available = _get_arango().count_edges(entity_id, types)
        except ArangoError:
            available = None
    result["truncation"] = {
        "truncated": fetched >= limit,
        "available": available,
        "returned": len(result["edges"]),
    }
    return result


@mcp.tool
async def kg_path(
    from_id: Annotated[str, Field(description="Source entity _id.")],
    to_id: Annotated[str, Field(description="Target entity _id.")],
    max_hops: Annotated[int, Field(ge=1, le=6, description="Max path length.")] = 4,
) -> dict[str, Any]:
    """Find the shortest connection between two known entities.

    Use this when you have two specific entity IDs and want to understand
    how they're related (e.g. "how does plugin X reach table Y?"). For
    open-ended neighborhood exploration use ``kg_neighbors`` instead.
    Returns ``{nodes:[], edges:[]}`` if no path exists within ``max_hops``.

    Grounding: path edges carry `source_quote` (verbatim, citable),
    `extraction_type`, and `releases` (but not `confidence`). Cite the quote,
    treat inferred edges as tentative, and scope claims to `releases`.

    Args:
        from_id: Source entity ArangoDB document ID.
        to_id: Target entity ArangoDB document ID.
        max_hops: Maximum number of hops to search (1-6).

    Returns:
        dict with keys nodes, edges, optional no_path flag, and optional error.
    """
    try:
        result = _get_arango().shortest_path(from_id, to_id, max_hops=max_hops)
        return result if result is not None else {"nodes": [], "edges": [], "no_path": True}
    except ArangoError as exc:
        return {"nodes": [], "edges": [], "error": str(exc)}


@mcp.tool
async def kg_schema() -> dict[str, Any]:
    """Discover entity types and relationship predicates in the ServiceNow KG.

    Call this FIRST before ``kg_neighbors`` whenever you want to filter
    traversal by predicate — without it, predicate filters are guesswork
    and unfiltered neighborhood retrievals can balloon past the consumer
    tool-result cap.

    Returns:
        dict with keys entity_types (list), predicates (list), and optional error.
    """
    try:
        return _get_arango().schema()
    except ArangoError as exc:
        return {"entity_types": [], "predicates": [], "error": str(exc)}


# --- Entry point ----------------------------------------------------------


def _is_loopback_host(url: str) -> bool:
    """True iff the URL's host is a loopback literal.

    Used to gate the remote-root refusal below: a bare hostname check, not a
    security boundary — it cannot see through an SSH tunnel (a tunnel
    presents as loopback on the client side even though Arango is remote).
    That residual is accepted; the gate exists to catch the common
    misconfiguration (BYO-prod Arango + default root creds), not to defeat a
    deliberately tunneled setup.

    Args:
        url: The Arango endpoint URL (e.g. "http://localhost:8529").

    Returns:
        True if the URL's hostname is a loopback literal.
    """
    host = urlparse(url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1")


def main() -> None:
    """Start the embeddington MCP server after running isolation sanity checks.

    Raises:
        SystemExit: If ARANGO_PASSWORD is missing, ARANGO_USER=root is used
            against a non-loopback ARANGO_URL without an explicit opt-in, or
            the isolation check fails.
    """
    if not config.ARANGO_PASSWORD:
        raise SystemExit(
            "Missing required env var: ARANGO_PASSWORD must be set "
            "(via claude_desktop_config.json or .env). "
            "See README.md for setup instructions."
        )

    if (
        config.ARANGO_USER == "root"
        and not _is_loopback_host(config.ARANGO_URL)
        and os.environ.get("EMBEDDINGTON_ALLOW_REMOTE_ROOT") != "1"
    ):
        raise SystemExit(
            "Refusing to start: ARANGO_USER=root against a remote Arango "
            f"({config.ARANGO_URL}). Use the scoped read-only user (kg_servicenow_ro) "
            "for remote/production stores. To deliberately accept the risk, set "
            "EMBEDDINGTON_ALLOW_REMOTE_ROOT=1."
        )

    # Sanity check before exposing tools
    asyncio.run(_isolation_sanity_check())

    # The sanity check ran inside its own asyncio.run() loop, which is now
    # closed. Any httpx.AsyncClient the singletons created during the check
    # is bound to that closed loop; reusing it on the first MCP request
    # raises "Event loop is closed". Discard the lazy singletons so the
    # first real request rebuilds them on FastMCP's running loop.
    global _embed_clients, _qdrant_clients
    _embed_clients = {}
    _qdrant_clients = {}

    logger.info("Starting embeddington MCP (stdio)")
    mcp.run()


if __name__ == "__main__":
    main()
