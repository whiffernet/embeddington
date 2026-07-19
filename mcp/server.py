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
import sys
from pathlib import Path
from typing import Annotated, Any, Optional

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
    from .embedding_client import EmbeddingClient, EmbeddingError
    from .enrich import enrich as _enrich_impl
    from .qdrant_client import QdrantError, QdrantSearchClient
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import config  # type: ignore[no-redef]
    from arango_client import ArangoError, ArangoKGClient  # type: ignore[no-redef]
    from embedding_client import EmbeddingClient, EmbeddingError  # type: ignore[no-redef]
    from enrich import enrich as _enrich_impl  # type: ignore[no-redef]
    from qdrant_client import QdrantError, QdrantSearchClient  # type: ignore[no-redef,attr-defined]

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
    """
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
    top_k: Annotated[int, Field(ge=1, le=50, description="Vector chunks to return.")] = 5,
    edge_budget: Annotated[
        int,
        Field(
            ge=1,
            le=200,
            description="TOTAL KG edges across the whole response, allocated "
            "across matched concepts. Truncation is explicit — see each "
            "match's truncation object and suggest hint. A larger budget "
            "returns more edges up to the response ceiling and then plateaus "
            "(the ceiling trim caps the total either way); the sweep default "
            "of 40 already sits at that plateau, so raising it mainly adds "
            "latency, not edges.",
        ),
    ] = 40,
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
    warnings}) — no synthesis. Claude does all reasoning over the returned
    data.

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
    rare case per-concept floors force a small overflow). Raising
    edge_budget spreads the budget across more matched concepts, which can
    DILUTE query relevance (measured: retention 0.282 -> 0.200 as
    edge_budget went 40 -> 120 at top_k=3 — see
    mcp/tests/battery_results/2026-07-17-sweep.md). Past ~40 it adds
    latency without adding delivered edges. For stronger KG grounding
    prefer LOWERING top_k rather than raising edge_budget.

    Args:
        query: The user's natural-language question.
        entity_hints: Entity names pre-extracted by Claude from the query.
        top_k: Number of vector chunks to return (1-50).
        edge_budget: Total KG edge slots to split across matched concepts
            (1-200, default 40).
        predicates: Optional relationship predicate filter. Unknown
            predicates (per kg_schema) are flagged in warnings, not rejected.

    Returns:
        dict with keys vector_chunks, kg_matches, errors, budget, warnings.
    """
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
    )
    result["warnings"] = server_warnings + result["warnings"]
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

    Args:
        query: Natural-language search query to embed and search.
        collection: Allowlisted collection name; defaults to technology (m3).
        limit: Maximum number of results to return (1-50).

    Returns:
        dict with keys results, count, collection, and optional error.
    """
    collection = collection or config.DEFAULT_QDRANT_COLLECTION
    if collection not in config.ALLOWED_QDRANT_COLLECTIONS:
        return {
            "results": [],
            "count": 0,
            "collection": collection,
            "error": f"unknown collection '{collection}'; allowed: "
            f"{sorted(config.ALLOWED_QDRANT_COLLECTIONS)}",
        }
    index = config.ALLOWED_QDRANT_COLLECTIONS[collection]
    try:
        vec = await _get_embed(index).embed(query)
        results = await _get_qdrant(collection).search(vector=vec, limit=limit)
        return {"results": results, "count": len(results), "collection": collection}
    except (EmbeddingError, QdrantError) as exc:
        return {"results": [], "count": 0, "collection": collection, "error": str(exc)}


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


def main() -> None:
    """Start the embeddington MCP server after running isolation sanity checks.

    Raises:
        SystemExit: If ARANGO_PASSWORD is missing or isolation check fails.
    """
    if not config.ARANGO_PASSWORD:
        raise SystemExit(
            "Missing required env var: ARANGO_PASSWORD must be set "
            "(via claude_desktop_config.json or .env). "
            "See README.md for setup instructions."
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
