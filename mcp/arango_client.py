"""ArangoDB client for embeddington knowledge graph queries.

Wraps python-arango to query only ServiceNow KG collections. Which collections,
graph and search view those are is resolved once per client, at construction,
from `config.kg_collections()` — never hardcoded here (spec §9.2: the schema
knob that lets the same reader code run against either KG generation). All
queries are AQL templates with bound parameters — never string-interpolated
user input.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, cast

from arango import ArangoClient
from arango.cursor import Cursor
from arango.exceptions import ArangoError as _ArangoError
from arango.exceptions import DocumentGetError

# Same dual-context import shim as server.py — supports both package import
# (tests, python -m) and direct script invocation (when server.py is run as
# a file path by Claude Desktop, this module gets loaded via sys.path).
try:
    from . import config
except ImportError:
    import config  # type: ignore[no-redef,import-not-found]

logger = logging.getLogger("embeddington.arango")

# The scan query ran a 1-hop degree traversal for EVERY substring match (thousands for a
# short needle); the view query ranks first and traverses at most this many survivors.
FIND_CANDIDATE_CAP = 200

# --- kg_path abstention (spec §6; round A labels: hub-mediated paths bad ~86%, release-
# mediated 5/5 bad; diagnosis "abstention, not ranking") ------------------------------
# Degree distribution on prod, 2026-09-01: median 1, p95 13, p99 46, p999 236, max 26,602
# (role__admin, 3.2% of all edges). 1000 ≈ 4× p999 flags only the extreme tail — the top few
# dozen vertices (role__admin, the four Release nodes at 3–6k, table__incident 5,937,
# feature__now_assist 4,145, table__sys_properties 4,069, table__cmdb_ci 4,031, ...) — which
# matches the battery's hub_count of 32. Starting value; re-derive from the v3 doctor's
# hub-share metric. Applies to INTERMEDIATE vertices only; endpoints may be hubs.
PATH_HUB_DEGREE_CEILING = 1000
# Vertex types never allowed as an intermediate. A Release node connects everything
# INTRODUCED_IN the same release; that is co-occurrence, not a relationship.
PATH_EXCLUDED_INTERMEDIATE_TYPES = frozenset({"Release"})
# Candidate paths enumerated (ascending length) before giving up. Yen's algorithm pays one
# shortest-path search per candidate; 20 keeps a hub-adjacent pair under ~100 ms.
PATH_CANDIDATE_CAP = 20

_PROVENANCE_COUNT_AQL_V2 = "LENGTH({e}.releases || [])"
_PROVENANCE_COUNT_AQL_V3 = "LENGTH({e}.provenance || [])"


def _provenance_count_aql(e: str) -> str:
    """AQL fragment for the provenance-count sort key used as the second tier
    in `neighbors()`'s tiebreak (pool SORT and final SORT, spec §6).

    v2 has NO provenance array (an edge records only its first asserter, see
    spec §1); `LENGTH(releases)` is the closest available signal -- an edge
    re-asserted by a second release carries two entries. v3 edges carry a
    real per-assertion `provenance` array (Track 2, spec §2), so under
    `EMBEDDINGTON_KG_SCHEMA=v3` this switches to `LENGTH(provenance)` instead
    -- without it the `limit` cap would keep a different edge set than
    RESPONSE_SHAPES documents once `v3` is selectable, a silent ranking
    degradation rather than an error.

    Read from `config.KG_SCHEMA` at CALL time, not at module-import time, so
    the schema can be monkeypatched in tests without reloading this module.

    Args:
        e: The AQL variable name for the edge document (``"e"`` in the pool
            query, ``"r.edge"`` in the final sort).

    Returns:
        The AQL fragment, e.g. ``"LENGTH(e.releases || [])"``.
    """
    template = _PROVENANCE_COUNT_AQL_V3 if config.KG_SCHEMA == "v3" else _PROVENANCE_COUNT_AQL_V2
    return template.format(e=e)


# Same value and rationale as neighbors_stratified's pool cap: keep hub memory sane. Applied
# after the pool's confidence/provenance SORT (so it keeps the top band) and before the
# per-predicate COLLECT/rank, so a hub over this size is ranked over its top-N candidates
# rather than every traversal row — same trade-off neighbors_stratified already makes.
NEIGHBORS_POOL_CAP = 5000

# --- Track 2 coverage/provenance (spec §2, §9.2) ----------------------------
# v3 edges carry `origins` (a pre-sorted, deduped list of provenance origins,
# e.g. ["markdown"] or ["pdf-legacy", "markdown"]) and `provenance` (the full
# per-assertion array llamaindex's writer appends to). v2 edges have neither
# field at all -- AQL's `|| []` coalesce is what makes the same query template
# correct against both schemas (R3: a v2-shaped row yields origins == [] and
# no provenance to rank, never an error).
#
# "pdf-legacy" marks a provenance entry seeded from the pre-v3 PDF corpus
# migration, not a live re-extraction. `coverage_only=True` (R2) is "give me
# edges attested by something OTHER than that backfill" -- it excludes a row
# ONLY when EVERY origin it has is "pdf-legacy"; a row with no origins at all
# (v2, or a v3 row the writer never annotated) is not pdf-legacy-only and must
# never be excluded by this filter.
#
# One template, three call sites: neighbors(), neighbors_stratified() apply it
# verbatim inside their single-edge FOR loop; shortest_path() adapts it to
# require EVERY edge on a candidate path to pass, since a path is only as
# trustworthy as its weakest edge.
COVERAGE_ONLY_EDGE_FILTER = (
    "FILTER !@coverage_only OR LENGTH(e.origins || []) == 0 "
    'OR LENGTH(FOR o IN (e.origins || []) FILTER o != "pdf-legacy" RETURN 1) > 0'
)

# The 9-key subset of a provenance entry `_best_provenance` returns -- a
# deliberate cut-down of the full per-assertion record (which also carries
# `window`, `window_size`, `window_overlap`, `pass`, `pass_hits`) to the
# fields a caller needs to judge and cite the winning assertion.
_PROVENANCE_VIEW_KEYS = (
    "origin",
    "source_document",
    "release",
    "file_hash",
    "chunk_ids",
    "confidence",
    "source_quote",
    "extraction_recipe",
    "extracted_at",
)


def _entry_outranks(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True if provenance entry ``a`` outranks ``b`` (mirrors llamaindex's
    ``writer._entry_rank``, spec §2).

    Priority, each tier breaking ties in the previous one only:
      1. Any origin other than "pdf-legacy" beats "pdf-legacy", full stop --
         a live re-extraction is preferred over the PDF-corpus backfill
         regardless of every other signal.
      2. Higher `confidence` wins.
      3. `extraction_type == "explicit"` beats "inferred".
      4. A later `extracted_at` wins (most recent assertion).
      5. A lexicographically LOWER `source_document` wins -- the only tier
         that favors the smaller value, purely for deterministic output when
         every other signal ties.

    Args:
        a: Candidate provenance entry.
        b: Current best provenance entry.

    Returns:
        True iff `a` should replace `b` as the best entry.
    """
    a_live = a.get("origin") != "pdf-legacy"
    b_live = b.get("origin") != "pdf-legacy"
    if a_live != b_live:
        return a_live

    a_conf = a.get("confidence") or 0.0
    b_conf = b.get("confidence") or 0.0
    if a_conf != b_conf:
        return a_conf > b_conf

    a_explicit = a.get("extraction_type") == "explicit"
    b_explicit = b.get("extraction_type") == "explicit"
    if a_explicit != b_explicit:
        return a_explicit

    a_time = a.get("extracted_at") or ""
    b_time = b.get("extracted_at") or ""
    if a_time != b_time:
        return a_time > b_time

    a_doc = a.get("source_document") or ""
    b_doc = b.get("source_document") or ""
    if a_doc != b_doc:
        return a_doc < b_doc

    return False  # fully tied -- keep the incumbent (stable)


def _best_provenance(entries: Optional[list[dict[str, Any]]]) -> Optional[dict[str, Any]]:
    """Pick the single most-trustworthy provenance entry for an edge.

    Applies the same size guards every sibling edge shape already applies to
    its own `source_quote`/list fields (`SUBSTRING(e.source_quote, 0, 240)`
    in both pool queries, `[:240]` in `_render_path`, `SLICE(..., 0, 5)` on
    `source_documents`) -- without them, a v3 edge's `best_provenance` would
    be the one place an untruncated quote or an unbounded `chunk_ids` list
    could reach a caller.

    Args:
        entries: The edge's raw `provenance` array, or None/[] for a
            v2-shaped edge (R3) or a v3 edge the writer never annotated.

    Returns:
        The winning entry projected to `_PROVENANCE_VIEW_KEYS`, with
        `source_quote` truncated to 240 chars and `chunk_ids` capped to its
        first 5 entries (both left as-is if absent/None), or None if
        `entries` is empty.
    """
    if not entries:
        return None
    best = entries[0]
    for entry in entries[1:]:
        if _entry_outranks(entry, best):
            best = entry
    view = {k: best.get(k) for k in _PROVENANCE_VIEW_KEYS}
    quote = view.get("source_quote")
    if quote is not None:
        view["source_quote"] = quote[:240]
    chunk_ids = view.get("chunk_ids")
    if chunk_ids is not None:
        view["chunk_ids"] = list(chunk_ids)[:5]
    return view


def _edge_view(e: dict[str, Any]) -> dict[str, Any]:
    """Coverage-tier fields every edge response shape adds (spec §2, §6).

    Called by every RETURN shape that builds an edge dict (`neighbors`,
    `neighbors_stratified`, `_render_path`) against the raw AQL/document edge
    `e`, so the origins/best_provenance derivation lives in exactly one place
    instead of being reimplemented at each call site.

    Track 2 is v3-only (spec §6): an edge with neither field yields an EMPTY
    dict here, not `{"origins": [], "best_provenance": None}` -- the caller
    must merge this in without adding those keys at all, so a v2 (or
    schema-with-no-provenance-model) response is byte-identical in shape and
    size to pre-Track-2 behavior. This mattered in practice: emitting the two
    keys unconditionally added ~37 bytes/edge, which pushed `enrich`'s
    token-ceiling trim to drop 2-4 tail edges per query even under v2 (Task 3
    fix round 1, Important 1).

    Args:
        e: Raw edge document/row, exposing (or, on a v2-shaped edge, lacking)
            `origins` and `provenance`.

    Returns:
        `{}` when `e` carries neither `origins` nor `provenance`. Otherwise
        ``{"origins": list[str], "best_provenance": dict | None}`` to merge
        into whatever edge shape the caller is building.
    """
    origins = e.get("origins")
    provenance = e.get("provenance")
    if origins is None and provenance is None:
        return {}
    return {
        "origins": origins or [],
        "best_provenance": _best_provenance(provenance),
    }


def _apply_edge_view(e: dict[str, Any]) -> None:
    """Merge `_edge_view(e)` into `e` in place, then drop the raw AQL
    projection keys `_edge_view` read from.

    `neighbors()`/`neighbors_stratified()` always project `origins:
    e.origins, provenance: e.provenance` in their AQL `RETURN` (AQL member
    access on a missing field yields `null`, not an absent key), so on a
    v2-shaped edge `e` already carries a stray `"origins": None` even though
    `_edge_view` correctly returned `{}` for it. This strips that stray key
    too, so the final edge dict has no Track-2 keys at all on v2 -- not
    `origins: None` and not `origins: []`.

    Args:
        e: The edge dict to update in place. Must already carry the raw
            `origins`/`provenance` keys the AQL RETURN projects.
    """
    e.update(_edge_view(e))
    e.pop("provenance", None)
    if e.get("origins") is None:
        e.pop("origins", None)


class ArangoError(Exception):
    """Raised on Arango query failure."""


class ArangoKGClient:
    """Query interface for the ServiceNow knowledge graph.

    Queries only the KG collections (entities, relationships, graph, search
    view) named by `config.kg_collections()` for the active `config.KG_SCHEMA`
    — resolved once, here, at construction, never re-read per query and never
    hardcoded in a query template.

    Args:
        url: ArangoDB endpoint (e.g. http://localhost:8529).
        database: Target database (default: technology_kg).
        username: Credentials for accessing the KG database.
        password: User's password.
        timeout: Per-request timeout in seconds, covering connect and read. The other two
            backends already take one; this one used to inherit the driver's 60s default.
    """

    def __init__(
        self,
        url: str,
        database: str,
        username: str,
        password: str,
        timeout: float = 30.0,
    ) -> None:
        # [CRITIC] Without an explicit timeout this inherits python-arango's 60s default,
        # and an Arango that accepts connections without answering — exactly what WAL
        # replay after an unclean stop looks like, and what a disk-full or OOM-throttled
        # container looks like — stalls every call for the full 60s. enrich makes several
        # SERIAL calls (find_entities per hint, then neighbors_stratified per variant), so
        # one tool call could hang for minutes.
        #
        # That matters because this module is built to DEGRADE: enrich catches ArangoError
        # and returns the vector half with a grounding tier saying what is missing. A hang
        # raises nothing, so instead of a degraded answer the caller just waits.
        self._client = ArangoClient(hosts=url, request_timeout=timeout)
        self._db = self._client.db(database, username=username, password=password)
        self._database = database
        self._search_view_available: Optional[bool] = None  # probed lazily, once
        # Resolved once here, from the schema active at construction time — not
        # re-read per query. A process that needs the other schema restarts
        # with EMBEDDINGTON_KG_SCHEMA set differently; this client never mixes
        # collections from two schemas in one query.
        schema = config.kg_collections()
        self._entities = schema["entities"]
        self._relationships = schema["relationships"]
        self._graph = schema["graph"]
        self._search_view = schema["search_view"]

    def _view_available(self) -> bool:
        """Whether the active schema's search view exists in this database (probed once).

        Returns:
            True if the view exists and the probe succeeded; False otherwise
            (absent view, or a driver error on the probe — either way the scan
            query is used, and the choice is logged once).
        """
        if self._search_view_available is None:
            try:
                try:
                    # python-arango (8.3.5, the latest release) has no has_view() on
                    # StandardDatabase — only views()/view_info(). A real driver
                    # instance raises AttributeError here, caught below and retried
                    # against the real list API. Kept as the first attempt (rather
                    # than removed) so this stays a single probe against a test
                    # double that stands in for ``_db`` wholesale and defines
                    # has_view (a MagicMock, or a future driver release that adds
                    # the method).
                    self._search_view_available = bool(
                        self._db.has_view(self._search_view)  # type: ignore[attr-defined]
                    )
                except AttributeError:
                    # views() is typed as returning a sync/async/batch union because
                    # the same client class backs all three execution modes; this
                    # client only ever runs synchronously, so the result is a plain list.
                    names = {v["name"] for v in cast(list[dict[str, Any]], self._db.views())}
                    self._search_view_available = self._search_view in names
            except _ArangoError as exc:
                logger.warning("view probe failed (%s); find_entities uses the scan query", exc)
                self._search_view_available = False
            if not self._search_view_available:
                logger.warning(
                    "%s absent in %s — find_entities uses the full-scan seed query",
                    self._search_view,
                    self._database,
                )
        return self._search_view_available

    @staticmethod
    def _like_pattern(needle_lc: str) -> str:
        """Wrap a lowercased needle as a ``%needle%`` LIKE pattern, escaping wildcards.

        Args:
            needle_lc: The already-lowercased search text.

        Returns:
            A pattern where ``%``, ``_`` and ``\\`` in the needle are backslash-escaped
            so ``sys_user`` matches literally instead of ``sys?user``.
        """
        return "%" + re.sub(r"([\\%_])", r"\\\1", needle_lc) + "%"

    def _find_entities_query(self, use_view: bool) -> str:
        """Build the seed query for ``find_entities``.

        Args:
            use_view: True for the ArangoSearch-view query, False for the legacy
                collection scan (kept verbatim as the fallback and the bench "before").

        Returns:
            The AQL text. Bind variables come from ``_find_entities_bind_vars``.
        """
        return_block = """
            RETURN {
                id: e._id,
                name: e.name,
                type: e.type,
                source_documents: SLICE(e.source_documents, 0, 5),
                releases: e.releases,
                updated_at: e.updated_at,
                degree: degree,
            }"""
        if not use_view:
            return f"""
        FOR e IN {self._entities}
            FILTER CONTAINS(LOWER(e.name), @needle_lc)
            LET nm = LOWER(e.name)
            LET match_rank = nm == @needle_lc ? 3 : (STARTS_WITH(nm, @needle_lc) ? 2 : 1)
            LET degree = LENGTH(FOR x IN 1..1 ANY e GRAPH @graph RETURN 1)
            SORT match_rank DESC, degree DESC
            LIMIT @limit
            {return_block}
        """
        # norm_en: exact, prefix and (wildcard-escaped) substring on the whole lowercased
        # name; text_en: every prose token present (stemmed) — rank 0, below substring.
        # Candidates are ranked and capped BEFORE the per-survivor degree traversal.
        norm = f"{self._database}::norm_en"
        return f"""
        LET cands = (
            FOR c IN {self._search_view}
                SEARCH ANALYZER(
                        c.name == @needle_lc
                        OR STARTS_WITH(c.name, @needle_lc)
                        OR LIKE(c.name, @needle_like),
                    "{norm}")
                    OR ANALYZER(TOKENS(@needle, "text_en") ALL == c.name, "text_en")
                LET nm = LOWER(c.name)
                LET match_rank = nm == @needle_lc ? 3
                    : (STARTS_WITH(nm, @needle_lc) ? 2 : (CONTAINS(nm, @needle_lc) ? 1 : 0))
                SORT match_rank DESC, BM25(c) DESC
                LIMIT @cand_cap
                RETURN {{e: c, match_rank: match_rank}}
        )
        FOR x IN cands
            LET e = x.e
            LET degree = LENGTH(FOR n IN 1..1 ANY e GRAPH @graph RETURN 1)
            SORT x.match_rank DESC, degree DESC
            LIMIT @limit
            {return_block}
        """

    def _find_entities_bind_vars(self, text: str, limit: int, use_view: bool) -> dict[str, Any]:
        """Bind variables for ``_find_entities_query`` — exactly the set each query uses.

        Args:
            text: Raw search needle.
            limit: Result cap.
            use_view: Must match the argument given to ``_find_entities_query``.

        Returns:
            The bind dict. AQL rejects declared-but-unused bind variables, so the
            view-only keys are added only for the view query.
        """
        needle_lc = text.lower()
        bind: dict[str, Any] = {"needle_lc": needle_lc, "limit": limit, "graph": self._graph}
        if use_view:
            bind["needle"] = text
            bind["needle_like"] = self._like_pattern(needle_lc)
            bind["cand_cap"] = FIND_CANDIDATE_CAP
        return bind

    def find_entities(self, text: str, limit: int = 10) -> list[dict[str, Any]]:
        """Fuzzy match on entity name, seeded from the ArangoSearch view when present.

        Results are relevance-ranked: exact name match first, then prefix,
        then substring, then prose-token match; ties broken by graph degree
        (descending) so the core hub entity wins over peripheral matches. With
        the view, the degree traversal runs over at most ``FIND_CANDIDATE_CAP``
        BM25-ranked survivors instead of every substring match in the
        collection (the pre-view query was a 359k-document scan per call).
        Without the view (consumer installs) the legacy scan runs unchanged.
        The view is eventually consistent (~1 s commit interval), so an entity
        written milliseconds ago may not seed yet.

        Args:
            text: Search needle — matched case-insensitively against the entity name.
            limit: Maximum number of results to return.

        Returns:
            List of dicts with keys ``id``, ``name``, ``type``,
            ``source_documents`` (first 5, for citation), ``releases``,
            ``updated_at`` (recency metadata, not a ranking signal) and
            ``degree`` (1-hop edge count, computed here so callers need no
            second traversal).

        Raises:
            ArangoError: On query failure.
        """
        use_view = self._view_available()
        try:
            cursor = self._db.aql.execute(
                self._find_entities_query(use_view),
                bind_vars=self._find_entities_bind_vars(text, limit, use_view),
            )
            return list(cast(Cursor, cursor))
        except _ArangoError as exc:
            raise ArangoError(f"find_entities failed: {exc}") from exc

    def get_entity(self, entity_id: str) -> Optional[dict[str, Any]]:
        """Fetch a full entity document by _id (e.g. '<entities-collection>/abc123' —
        see `kg_schema()` for the active schema's collection name).

        Args:
            entity_id: Full ArangoDB document ID including collection prefix.

        Returns:
            Dict with ``id`` plus all non-private document fields, or ``None``
            if the document does not exist.

        Raises:
            ArangoError: If ``entity_id`` is malformed or on query failure.
        """
        if "/" not in entity_id:
            raise ArangoError(f"invalid entity_id (must include collection): {entity_id}")
        try:
            doc = self._db.collection(self._entities).get(entity_id.split("/", 1)[1])
        except DocumentGetError:
            return None
        except _ArangoError as exc:
            raise ArangoError(f"get_entity failed: {exc}") from exc
        if doc is None:
            return None
        doc_dict = cast(dict[str, Any], doc)
        return {
            "id": doc_dict["_id"],
            **{k: v for k, v in doc_dict.items() if not k.startswith("_")},
        }

    def neighbors(
        self,
        entity_id: str,
        depth: int = 1,
        types: Optional[list[str]] = None,
        limit: int = 100,
        coverage_only: bool = False,
    ) -> dict[str, Any]:
        """Return connected entities (any direction) and the edges that connect them.

        Args:
            entity_id: Starting vertex ``_id``.
            depth: Traversal depth (clamped to 1–3).
            types: Optional list of predicate names to filter edges.
            limit: Cap on raw (vertex, edge) traversal rows. After dedup
                the returned ``nodes`` list may be shorter. Default 100 is
                chosen to keep the JSON response under Claude Code's
                ~75-100 KB single-tool-result cap; raise for broad
                exploration only when needed.
            coverage_only: When True, drop an edge whose ``origins`` are
                entirely ``"pdf-legacy"`` (R2) — never drops an edge with no
                ``origins`` at all (v2, or an unannotated v3 edge). No effect
                on a v2-shaped graph, where no edge has ``origins``.

        Returns:
            Dict with ``nodes`` (``{id, name, type, releases, updated_at}``
            vertex dicts), ``edges`` (``{id, source, target, predicate,
            confidence, extraction_type, releases, source_document,
            source_quote, updated_at, origins, best_provenance}`` dicts — see
            ``_edge_view`` for the last two), and ``fetched`` (raw pre-dedup
            traversal row count — lets callers tell "truncated by limit"
            apart from "genuinely small neighborhood"). ``releases`` gives
            ServiceNow version context; ``updated_at`` is an ISO timestamp of
            last KG write (recency metadata, not a ranking signal);
            ``extraction_type`` ("explicit"/"inferred") pairs with
            ``confidence`` as a reliability signal; ``source_quote`` is
            verbatim provenance truncated to 240 chars so a dense
            neighborhood stays under the consumer tool-result cap.
            Edges are ordered by ``confidence`` DESC, then provenance count
            (``LENGTH(releases)`` on v2, ``LENGTH(provenance)`` on v3 — see
            ``_provenance_count_aql``) DESC, then per-predicate rank ASC — so
            when ``limit`` truncates a hub the cap keeps the most-reliable,
            best-attested edges and interleaves predicates within a tied
            band instead of taking one predicate's slice. On hubs whose
            depth-N neighborhood exceeds
            ``NEIGHBORS_POOL_CAP`` (5000) candidate rows, the confidence/
            provenance sort and predicate ranking are computed over only the
            top ``NEIGHBORS_POOL_CAP`` by confidence/provenance — the same
            hub-memory trade-off ``neighbors_stratified`` already makes.
            ``nodes``/``edges`` are deduplicated by id; ``fetched`` counts raw
            rows before that dedup.

        Raises:
            ArangoError: On query failure.
        """
        depth = max(1, min(depth, 3))  # safety cap
        limit = max(1, min(limit, 500))  # safety cap
        type_filter = ""
        bind_vars: dict[str, Any] = {
            "start": entity_id,
            "graph": self._graph,
            "depth": depth,
            "row_cap": limit,
            "pool_cap": NEIGHBORS_POOL_CAP,
            "coverage_only": coverage_only,
        }
        if types:
            type_filter = "FILTER e.predicate IN @types"
            bind_vars["types"] = types

        # Three sort keys before the cap: confidence (88.7% of edges sit in the 0.9 band, so
        # alone it leaves the cap an arbitrary slice), provenance count, then per-predicate
        # rank so a tied band interleaves predicates instead of returning 100 CONTAINS rows.
        # COLLECT ... INTO preserves the pool's order within each group, so grp[i] is the
        # i-th best row of that predicate. pool_cap (applied after the pool's SORT, so it
        # keeps the top band, and before the COLLECT/rank) bounds a depth>1 hub traversal —
        # without it a depth-3 walk from a 26k-degree hub has no bound at all.
        prov_e = _provenance_count_aql("e")
        prov_r = _provenance_count_aql("r.edge")
        query = f"""
        LET pool = (
            FOR v, e IN 1..@depth ANY @start GRAPH @graph
                {type_filter}
                {COVERAGE_ONLY_EDGE_FILTER}
                SORT e.confidence DESC, {prov_e} DESC
                LIMIT @pool_cap
                RETURN {{
                    vertex: {{
                        id: v._id, name: v.name, type: v.type,
                        releases: v.releases, updated_at: v.updated_at,
                    }},
                    edge: {{
                        id: e._key,
                        source: e._from,
                        target: e._to,
                        predicate: e.predicate,
                        confidence: e.confidence,
                        extraction_type: e.extraction_type,
                        releases: e.releases,
                        source_document: e.source_document,
                        source_quote: SUBSTRING(e.source_quote, 0, 240),
                        updated_at: e.updated_at,
                        origins: e.origins,
                        provenance: e.provenance,
                    }}
                }}
        )
        LET ranked = FLATTEN(
            FOR r IN pool
                COLLECT p = r.edge.predicate INTO grp
                RETURN (FOR i IN 0..(LENGTH(grp) - 1) RETURN MERGE(grp[i].r, {{pred_rank: i}})),
            1)
        FOR r IN ranked
            SORT r.edge.confidence DESC, {prov_r} DESC, r.pred_rank ASC
            LIMIT @row_cap
            RETURN {{vertex: r.vertex, edge: r.edge}}
        """
        try:
            cursor = self._db.aql.execute(query, bind_vars=bind_vars)
            results = list(cast(Cursor, cursor))
        except _ArangoError as exc:
            raise ArangoError(f"neighbors failed: {exc}") from exc

        nodes: dict[str, dict] = {}
        edges: dict[str, dict] = {}
        for r in results:
            v = r["vertex"]
            e = r["edge"]
            _apply_edge_view(e)
            nodes.setdefault(v["id"], v)
            edges.setdefault(e["id"], e)
        return {
            "nodes": list(nodes.values()),
            "edges": list(edges.values()),
            "fetched": len(results),
        }

    def neighbors_stratified(
        self,
        entity_id: str,
        per_predicate: int = 2,
        overall: int = 50,
        predicates: Optional[list[str]] = None,
        coverage_only: bool = False,
    ) -> dict[str, Any]:
        """Depth-1 neighborhood sampled for predicate diversity (spec §3.3).

        One AQL pass over the (bounded) neighborhood: the top `per_predicate`
        edges per distinct predicate UNION the overall top `overall` by
        confidence. Null confidence coalesces to 0.5 for ORDERING only — the
        returned edge keeps its real (possibly null) value. The scan is
        bounded at 5000 rows by coalesced confidence to keep hub memory sane;
        `fetched` reports rows seen (callers derive availability from
        find_entities degree, not from fetched).

        Args:
            entity_id: Starting vertex ``_id``.
            per_predicate: Edges kept per distinct predicate (>=1).
            overall: Overall top-N by confidence to union in.
            predicates: Optional predicate filter, case-insensitive.
            coverage_only: When True, drop an edge whose ``origins`` are
                entirely ``"pdf-legacy"`` (R2) — never drops an edge with no
                ``origins`` at all. No effect on a v2-shaped graph.

        Returns:
            ``{nodes, edges, fetched}`` — same node/edge shapes as neighbors()
            (including ``updated_at``, ``origins``, ``best_provenance`` on
            edges — see ``_edge_view``).

        Raises:
            ArangoError: On query failure.
        """
        per_predicate = max(1, min(per_predicate, 10))
        overall = max(1, min(overall, 500))
        pred_filter = ""
        bind_vars: dict[str, Any] = {
            "start": entity_id,
            "graph": self._graph,
            "pp": per_predicate,
            "overall": overall,
            "coverage_only": coverage_only,
        }
        if predicates:
            pred_filter = "FILTER UPPER(e.predicate) IN @preds"
            bind_vars["preds"] = [p.upper() for p in predicates]
        # NB: by_pred's inner COLLECT relies on `pool` already being
        # confidence-ordered — AQL COLLECT ... INTO preserves the input
        # order within each group, so SLICE(grp[*].r, 0, @pp) takes each
        # predicate's best rows without a re-sort. VERIFIED on the Tier-2
        # live battery (Task 10) against ArangoDB 3.12.4: for high-degree
        # hubs (CMDB deg 459 / 11 predicates, Incident deg 5315 / pool 5000)
        # the COLLECT+SLICE per-predicate picks matched an explicit
        # inner-SORT top-@pp-by-confidence for every predicate — order is
        # preserved, so no inner SORT is needed.
        query = f"""
        LET pool = (
            FOR v, e IN 1..1 ANY @start GRAPH @graph
                {pred_filter}
                {COVERAGE_ONLY_EDGE_FILTER}
                SORT e.confidence == null ? 0.5 : e.confidence DESC
                LIMIT 5000
                RETURN {{
                    vertex: {{
                        id: v._id, name: v.name, type: v.type,
                        releases: v.releases, updated_at: v.updated_at,
                    }},
                    edge: {{
                        id: e._key, source: e._from, target: e._to,
                        predicate: e.predicate, confidence: e.confidence,
                        extraction_type: e.extraction_type, releases: e.releases,
                        source_document: e.source_document,
                        source_quote: SUBSTRING(e.source_quote, 0, 240),
                        updated_at: e.updated_at,
                        origins: e.origins,
                        provenance: e.provenance,
                    }},
                }}
        )
        LET by_pred = FLATTEN(
            FOR r IN pool
                COLLECT p = r.edge.predicate INTO grp
                RETURN SLICE(grp[*].r, 0, @pp),
            1)
        LET top_overall = SLICE(pool, 0, @overall)
        FOR r IN UNION(by_pred, top_overall)
            COLLECT eid = r.edge.id INTO rows
            RETURN {{vertex: rows[0].r.vertex, edge: rows[0].r.edge, fetched: LENGTH(pool)}}
        """
        try:
            cursor = self._db.aql.execute(query, bind_vars=bind_vars)
            results = list(cast(Cursor, cursor))
        except _ArangoError as exc:
            raise ArangoError(f"neighbors_stratified failed: {exc}") from exc
        nodes: dict[str, dict] = {}
        edges: dict[str, dict] = {}
        fetched = 0
        for r in results:
            nodes.setdefault(r["vertex"]["id"], r["vertex"])
            e = r["edge"]
            _apply_edge_view(e)
            edges.setdefault(e["id"], e)
            fetched = max(fetched, r.get("fetched", 0))
        return {"nodes": list(nodes.values()), "edges": list(edges.values()), "fetched": fetched}

    def count_edges(self, entity_id: str, predicates: Optional[list[str]] = None) -> int:
        """Count depth-1 incident edges, optionally predicate-filtered.

        Used only when a predicate filter makes find_entities' degree the
        wrong availability basis (spec §5.3) — an unfiltered call should use
        degree instead of paying this traversal.

        Args:
            entity_id: Starting vertex ``_id``.
            predicates: Optional predicate filter, case-insensitive.

        Returns:
            Count of depth-1 incident edges matching the filter.

        Raises:
            ArangoError: On query failure.
        """
        pred_filter = ""
        bind_vars: dict[str, Any] = {"start": entity_id, "graph": self._graph}
        if predicates:
            pred_filter = "FILTER UPPER(e.predicate) IN @preds"
            bind_vars["preds"] = [p.upper() for p in predicates]
        query = f"""
        FOR v, e IN 1..1 ANY @start GRAPH @graph
            {pred_filter}
            COLLECT WITH COUNT INTO c
            RETURN c
        """
        try:
            cursor = self._db.aql.execute(query, bind_vars=bind_vars)
            rows = list(cast(Cursor, cursor))
        except _ArangoError as exc:
            raise ArangoError(f"count_edges failed: {exc}") from exc
        return int(rows[0]) if rows else 0

    def _degrees(self, ids: list[str]) -> dict[str, int]:
        """1-hop degree for each vertex id (one query, edge-index lookups).

        Args:
            ids: Full vertex ``_id`` values.

        Returns:
            Mapping id -> degree; ids the query did not return map to 0 via ``.get``.

        Raises:
            ArangoError: On query failure.
        """
        if not ids:
            return {}
        query = """
        FOR vid IN @ids
            LET d = LENGTH(FOR x IN 1..1 ANY vid GRAPH @graph RETURN 1)
            RETURN {id: vid, degree: d}
        """
        try:
            cursor = self._db.aql.execute(query, bind_vars={"ids": ids, "graph": self._graph})
            rows = list(cast(Cursor, cursor))
        except _ArangoError as exc:
            raise ArangoError(f"degrees failed: {exc}") from exc
        return {r["id"]: int(r["degree"]) for r in rows}

    @staticmethod
    def _render_path(path: dict[str, Any]) -> dict[str, Any]:
        """Project a K_SHORTEST_PATHS path onto the ``{nodes, edges}`` response shape.

        Args:
            path: ``{"vertices": [doc, ...], "edges": [doc, ...]}`` as AQL returns it.

        Returns:
            ``nodes`` as ``{id, name, type, releases}``; ``edges`` as ``{source, target,
            predicate, extraction_type, releases, source_document, source_quote, origins,
            best_provenance}`` (see ``_edge_view``) with the quote truncated to 240 chars.
            No ``id``/``confidence`` on path edges.
        """
        return {
            "nodes": [
                {
                    "id": v["_id"],
                    "name": v.get("name"),
                    "type": v.get("type"),
                    "releases": v.get("releases"),
                }
                for v in path["vertices"]
            ],
            "edges": [
                {
                    "source": e["_from"],
                    "target": e["_to"],
                    "predicate": e.get("predicate"),
                    "extraction_type": e.get("extraction_type"),
                    "releases": e.get("releases"),
                    "source_document": e.get("source_document"),
                    "source_quote": (e.get("source_quote") or "")[:240],
                    **_edge_view(e),
                }
                for e in path["edges"]
            ],
        }

    def shortest_path(
        self, from_id: str, to_id: str, max_hops: int = 4, coverage_only: bool = False
    ) -> Optional[dict[str, Any]]:
        """Shortest USABLE path between two entities, or an explicit abstention.

        Enumerates up to ``PATH_CANDIDATE_CAP`` candidate paths in ascending
        length (``K_SHORTEST_PATHS``), drops those longer than ``max_hops``,
        suppresses any whose intermediate vertices include a type in
        ``PATH_EXCLUDED_INTERMEDIATE_TYPES``, then returns the first whose
        intermediates all have degree <= ``PATH_HUB_DEGREE_CEILING``. If
        candidates exist but none survive, the answer is an abstention with a
        reason — a hub-mediated path is not evidence of a relationship, and
        saying so is more useful than narrating one.

        Args:
            from_id: Starting vertex ``_id``.
            to_id: Target vertex ``_id``.
            max_hops: Discard paths longer than this (clamped to 1–6).
            coverage_only: When True, drop a candidate path if ANY of its
                edges has ``origins`` entirely ``"pdf-legacy"`` (R2) — a path
                is only as trustworthy as its weakest edge. Never drops a
                path over an edge with no ``origins`` at all. No effect on a
                v2-shaped graph.

        Returns:
            ``{nodes, edges}`` for a usable path; ``{nodes: [], edges: [],
            abstained: True, reason, hubs}`` when every candidate was
            suppressed (``hubs`` lists the over-ceiling intermediates as
            ``{id, name, type, degree}``, highest degree first, empty when only
            release suppression fired); ``None`` when no candidate exists
            within ``max_hops`` (this also covers every candidate being
            dropped by ``coverage_only`` before ``max_hops`` filtering).

        Raises:
            ArangoError: On query failure.
        """
        max_hops = max(1, min(max_hops, 6))  # safety cap
        # A path is only as trustworthy as its weakest edge: unlike neighbors()/
        # neighbors_stratified() (one edge per row), a path carries several, so
        # coverage_only excludes the WHOLE path if any single edge on it is
        # pdf-legacy-only -- same per-edge test as COVERAGE_ONLY_EDGE_FILTER,
        # applied across p.edges instead of a single-edge FOR variable.
        # Split across two adjacent literals (implicit concatenation) so the
        # over-100-char FILTER line fits ruff's E501 line-length gate without
        # changing a single character of the emitted query text -- the split
        # falls between "RETURN 1)" and " == 0", both otherwise untouched.
        query = (
            """
        FOR p IN ANY K_SHORTEST_PATHS @from TO @to GRAPH @graph
            FILTER !@coverage_only OR LENGTH(
                FOR e IN p.edges
                    FILTER LENGTH(e.origins || []) > 0
                        AND LENGTH(FOR o IN (e.origins || []) FILTER o != "pdf-legacy" RETURN 1)"""
            """ == 0
                    RETURN 1
            ) == 0
            LIMIT @cap
            RETURN {vertices: p.vertices, edges: p.edges}
        """
        )
        bind_vars: dict[str, Any] = {
            "from": from_id,
            "to": to_id,
            "graph": self._graph,
            "cap": PATH_CANDIDATE_CAP,
            "coverage_only": coverage_only,
        }
        try:
            cursor = self._db.aql.execute(query, bind_vars=bind_vars)
            paths = list(cast(Cursor, cursor))
        except _ArangoError as exc:
            raise ArangoError(f"shortest_path failed: {exc}") from exc

        candidates = [p for p in paths if len(p["edges"]) <= max_hops]
        if not candidates:
            return None

        release_mediated = 0
        survivors: list[dict[str, Any]] = []
        for p in candidates:
            inner = p["vertices"][1:-1]
            if any(v.get("type") in PATH_EXCLUDED_INTERMEDIATE_TYPES for v in inner):
                release_mediated += 1
            else:
                survivors.append(p)

        hubs: dict[str, dict[str, Any]] = {}
        if survivors:
            inner_ids = sorted({v["_id"] for p in survivors for v in p["vertices"][1:-1]})
            degrees = self._degrees(inner_ids)
            for p in survivors:
                over = [
                    v
                    for v in p["vertices"][1:-1]
                    if degrees.get(v["_id"], 0) > PATH_HUB_DEGREE_CEILING
                ]
                if not over:
                    return self._render_path(p)
                for v in over:
                    hubs.setdefault(
                        v["_id"],
                        {
                            "id": v["_id"],
                            "name": v.get("name"),
                            "type": v.get("type"),
                            "degree": degrees[v["_id"]],
                        },
                    )

        reason = (
            f"{len(candidates)} candidate path(s) within {max_hops} hops, none usable: "
            f"{release_mediated} release-mediated (suppressed), {len(survivors)} cross an "
            f"intermediate vertex above degree {PATH_HUB_DEGREE_CEILING}"
        )
        return {
            "nodes": [],
            "edges": [],
            "abstained": True,
            "reason": reason,
            "hubs": sorted(hubs.values(), key=lambda h: -h["degree"]),
        }

    def schema(self) -> dict[str, Any]:
        """Distinct entity types and predicate types in the KG.

        Returns:
            Dict with ``entity_types`` (sorted list of type strings) and
            ``predicates`` (sorted list of predicate strings).

        Raises:
            ArangoError: On query failure.
        """
        try:
            entity_types = list(
                cast(
                    Cursor,
                    self._db.aql.execute(f"FOR e IN {self._entities} COLLECT t = e.type RETURN t"),
                )
            )
            predicates = list(
                cast(
                    Cursor,
                    self._db.aql.execute(
                        f"FOR r IN {self._relationships} COLLECT p = r.predicate RETURN p"
                    ),
                )
            )
        except _ArangoError as exc:
            raise ArangoError(f"schema failed: {exc}") from exc
        return {"entity_types": sorted(entity_types), "predicates": sorted(predicates)}

    def probe_read(self) -> None:
        """Cheap allowlisted read used as a startup probe.

        Exercises real read access against the active schema's entities
        collection (LIMIT 1) so a wrong ARANGO_DATABASE or a missing/
        insufficient grant on the configured user surfaces as a boot-time
        signal, instead of as silent empty results the first time a tool
        queries the KG.

        Raises:
            Exception: Any failure from the underlying AQL execution (bad
                database, missing grant, connectivity). The caller treats
                this as a warn-only startup signal, never a hard failure.
        """
        self._db.aql.execute(f"FOR e IN {self._entities} LIMIT 1 RETURN 1")

    def can_read_collection(self, collection_name: str) -> bool:
        """Probe whether this client's user can read the given collection.

        Used by the startup isolation check. Returns True iff a count succeeds.

        Args:
            collection_name: Unqualified collection name to probe.

        Returns:
            ``True`` if the user can read the collection, ``False`` otherwise.
        """
        try:
            self._db.collection(collection_name).count()
            return True
        except _ArangoError:
            return False
