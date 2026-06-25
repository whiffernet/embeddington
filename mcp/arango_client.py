"""Scoped ArangoDB client for claudeGraph.

Wraps python-arango with a read-only user constrained to ServiceNow KG
collections. All queries are AQL templates with bound parameters — never
string-interpolated user input.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from arango import ArangoClient
from arango.exceptions import ArangoError as _ArangoError
from arango.exceptions import DocumentGetError

logger = logging.getLogger("claudegraph.arango")

ENTITIES = "entities_v2"
RELATIONSHIPS = "relationships_v2"
GRAPH = "servicenow_graph_v2"


class ArangoError(Exception):
    """Raised on Arango query failure."""


class ArangoKGClient:
    """Read-only client for the ServiceNow knowledge graph.

    Args:
        url: ArangoDB endpoint (e.g. http://localhost:8529).
        database: Target database (default: knowledge_graph).
        username: Scoped read-only user for the KG database.
        password: User's password.
    """

    def __init__(self, url: str, database: str, username: str, password: str) -> None:
        self._client = ArangoClient(hosts=url)
        self._db = self._client.db(database, username=username, password=password)

    def find_entities(self, text: str, limit: int = 10) -> list[dict[str, Any]]:
        """Fuzzy match on entity name.

        Args:
            text: Search needle — matched case-insensitively against the entity
                name.
            limit: Maximum number of results to return.

        Results are relevance-ranked: exact name match first, then prefix
        match, then substring; ties broken by graph degree (descending) so the
        core hub entity wins over peripheral matches. Without this, a bare
        ``CONTAINS ... LIMIT`` returns arbitrary peripheral nodes (e.g.
        "Discovery" → "/api/now/table/discovery_schedule" instead of the
        Discovery module), which then seeds KG traversal on the wrong
        neighborhood.

        Args:
            text: Search needle — matched case-insensitively against the entity
                name.
            limit: Maximum number of results to return.

        Returns:
            List of dicts with keys ``id``, ``name``, ``type``,
            ``source_documents`` (first 5 provenance docs, for citation) and
            ``releases`` (ServiceNow release tags, for version context). The
            legacy ``description`` key was removed — that attribute is empty on
            every entity in the corpus.

        Raises:
            ArangoError: On query failure.
        """
        # Degree is computed per candidate (one 1-hop traversal each), so a
        # high-frequency needle costs ~100ms; acceptable for the seeding step.
        # NB: the entity `description` attribute is empty corpus-wide, so the
        # filter is name-only.
        query = f"""
        FOR e IN {ENTITIES}
            FILTER CONTAINS(LOWER(e.name), LOWER(@needle))
            LET nm = LOWER(e.name)
            LET match_rank = nm == LOWER(@needle) ? 3 : (STARTS_WITH(nm, LOWER(@needle)) ? 2 : 1)
            LET degree = LENGTH(FOR x IN 1..1 ANY e GRAPH @graph RETURN 1)
            SORT match_rank DESC, degree DESC
            LIMIT @limit
            RETURN {{
                id: e._id,
                name: e.name,
                type: e.type,
                source_documents: SLICE(e.source_documents, 0, 5),
                releases: e.releases,
            }}
        """
        try:
            cursor = self._db.aql.execute(
                query,
                bind_vars={"needle": text, "limit": limit, "graph": GRAPH},
            )
            return list(cursor)
        except _ArangoError as exc:
            raise ArangoError(f"find_entities failed: {exc}") from exc

    def get_entity(self, entity_id: str) -> Optional[dict[str, Any]]:
        """Fetch a full entity document by _id (e.g. 'entities_v2/abc123').

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
            doc = self._db.collection(ENTITIES).get(entity_id.split("/", 1)[1])
        except DocumentGetError:
            return None
        except _ArangoError as exc:
            raise ArangoError(f"get_entity failed: {exc}") from exc
        if doc is None:
            return None
        return {
            "id": doc["_id"],
            **{k: v for k, v in doc.items() if not k.startswith("_")},
        }

    def neighbors(
        self,
        entity_id: str,
        depth: int = 1,
        types: Optional[list[str]] = None,
        limit: int = 100,
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

        Returns:
            Dict with ``nodes`` (``{id, name, type, releases}`` vertex dicts)
            and ``edges`` (``{id, source, target, predicate, confidence,
            extraction_type, releases, source_document, source_quote}`` dicts).
            ``releases`` gives ServiceNow version context; ``extraction_type``
            ("explicit"/"inferred") pairs with ``confidence`` as a reliability
            signal; ``source_quote`` is verbatim provenance truncated to 240
            chars so a dense neighborhood stays under the consumer tool-result
            cap. Edges are ordered highest-``confidence`` first, so when
            ``limit`` truncates a large neighborhood the most-reliable edges are
            kept. Duplicates are deduplicated by id.

        Raises:
            ArangoError: On query failure.
        """
        depth = max(1, min(depth, 3))  # safety cap
        limit = max(1, min(limit, 500))  # safety cap
        type_filter = ""
        bind_vars: dict[str, Any] = {
            "start": entity_id,
            "graph": GRAPH,
            "depth": depth,
            "row_cap": limit,
        }
        if types:
            type_filter = "FILTER e.predicate IN @types"
            bind_vars["types"] = types

        # SORT by confidence before the cap so a high-degree hub's row_cap
        # keeps its most-reliable edges instead of an arbitrary slice. Cheap at
        # depth 1 (the default); deeper traversals from a hub are large, hence
        # the row_cap. (Null confidence sorts last under DESC.)
        query = f"""
        FOR v, e IN 1..@depth ANY @start GRAPH @graph
            {type_filter}
            SORT e.confidence DESC
            LIMIT @row_cap
            RETURN {{
                vertex: {{id: v._id, name: v.name, type: v.type, releases: v.releases}},
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
                }}
            }}
        """
        try:
            cursor = self._db.aql.execute(query, bind_vars=bind_vars)
            results = list(cursor)
        except _ArangoError as exc:
            raise ArangoError(f"neighbors failed: {exc}") from exc

        nodes: dict[str, dict] = {}
        edges: dict[str, dict] = {}
        for r in results:
            v = r["vertex"]
            e = r["edge"]
            nodes.setdefault(v["id"], v)
            edges.setdefault(e["id"], e)
        return {"nodes": list(nodes.values()), "edges": list(edges.values())}

    def shortest_path(
        self, from_id: str, to_id: str, max_hops: int = 4
    ) -> Optional[dict[str, Any]]:
        """Shortest path between two entities.

        Args:
            from_id: Starting vertex ``_id``.
            to_id: Target vertex ``_id``.
            max_hops: Discard paths longer than this (clamped to 1–6).

        Returns:
            Dict with ``nodes`` (``{id, name, type, releases}``) and ``edges``
            (``{source, target, predicate, extraction_type, releases,
            source_document, source_quote}`` — no ``id``/``confidence``, unlike
            ``neighbors``) describing the path, or ``None`` if no path exists or
            the shortest path exceeds ``max_hops``. ``source_quote`` is
            truncated to 240 chars.

        Raises:
            ArangoError: On query failure.
        """
        max_hops = max(1, min(max_hops, 6))  # safety cap
        # SHORTEST_PATH doesn't accept maxLength options — we post-filter.
        query = """
        FOR v, e IN ANY SHORTEST_PATH @from TO @to GRAPH @graph
            RETURN {vertex: v, edge: e}
        """
        try:
            cursor = self._db.aql.execute(
                query,
                bind_vars={"from": from_id, "to": to_id, "graph": GRAPH},
            )
            steps = list(cursor)
        except _ArangoError as exc:
            raise ArangoError(f"shortest_path failed: {exc}") from exc

        if not steps:
            return None
        # steps[0] is the start vertex with edge=None; each subsequent step
        # has edge != None. Path length = number of non-null edges.
        edge_count = sum(1 for s in steps if s.get("edge") is not None)
        if edge_count > max_hops:
            return None
        return {
            "nodes": [
                {
                    "id": s["vertex"]["_id"],
                    "name": s["vertex"].get("name"),
                    "type": s["vertex"].get("type"),
                    "releases": s["vertex"].get("releases"),
                }
                for s in steps
            ],
            "edges": [
                {
                    "source": s["edge"]["_from"],
                    "target": s["edge"]["_to"],
                    "predicate": s["edge"].get("predicate"),
                    "extraction_type": s["edge"].get("extraction_type"),
                    "releases": s["edge"].get("releases"),
                    "source_document": s["edge"].get("source_document"),
                    "source_quote": (s["edge"].get("source_quote") or "")[:240],
                }
                for s in steps
                if s.get("edge") is not None
            ],
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
                self._db.aql.execute(f"FOR e IN {ENTITIES} COLLECT t = e.type RETURN t")
            )
            predicates = list(
                self._db.aql.execute(f"FOR r IN {RELATIONSHIPS} COLLECT p = r.predicate RETURN p")
            )
        except _ArangoError as exc:
            raise ArangoError(f"schema failed: {exc}") from exc
        return {"entity_types": sorted(entity_types), "predicates": sorted(predicates)}

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
