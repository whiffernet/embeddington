"""Tests for the scoped ArangoDB client.

Two families live here:

- Integration tests (the `client` fixture) run python-arango against a real
  Arango instance — they require ARANGO_TEST_URL + ARANGO_TEST_USER +
  ARANGO_TEST_PASSWORD in the env and skip (via the fixture) if not provided.
- Unit tests (the `kg_client` fixture) construct a real ArangoKGClient
  against a fake host — `ArangoClient.db()` defaults to `verify=False` so no
  network call is made — then replace `_db` with a MagicMock so
  `_db.aql.execute` can be stubbed and its call args asserted on. These
  always run; they exercise AQL construction, not a live server.
"""

import os
from unittest.mock import MagicMock

import pytest
from arango_client import NEIGHBORS_POOL_CAP, ArangoKGClient


@pytest.fixture
def client():
    if not os.environ.get("ARANGO_TEST_PASSWORD"):
        pytest.skip("set ARANGO_TEST_PASSWORD (and optional ARANGO_TEST_URL/USER) to run")
    return ArangoKGClient(
        url=os.environ.get("ARANGO_TEST_URL", "http://localhost:8529"),
        database="technology_kg",
        username=os.environ.get("ARANGO_TEST_USER", "root"),
        password=os.environ["ARANGO_TEST_PASSWORD"],
    )


@pytest.fixture
def kg_client():
    """ArangoKGClient with a mocked `_db` for AQL-construction unit tests."""
    c = ArangoKGClient(
        url="http://test-arango:8529",
        database="test_kg",
        username="test-user",
        password="test-pw",
    )
    c._db = MagicMock()
    return c


def test_find_entities_returns_results(client):
    results = client.find_entities("incident", limit=5)
    assert isinstance(results, list)
    # Don't assert non-empty — depends on KG data — but assert shape:
    for r in results:
        assert "id" in r
        assert "name" in r
        assert "type" in r
        # provenance/version fields replaced the (always-empty) description
        assert "source_documents" in r
        assert "releases" in r
        assert "description" not in r
        if r["source_documents"] is not None:
            assert isinstance(r["source_documents"], list)
            assert len(r["source_documents"]) <= 5  # capped to bound size


def test_find_entities_ranks_hub_over_peripheral(client):
    """Degree+exactness ranking must surface the core entity, not arbitrary
    peripheral substring matches. 'Discovery' should return the Discovery
    module/product, not a /api/.../discovery_schedule node."""
    results = client.find_entities("Discovery", limit=3)
    if not results:
        pytest.skip("no 'Discovery' entities in this KG")
    # An exact name match (match_rank=3) must rank ahead of substring matches.
    assert results[0]["name"] == "Discovery", (
        f"expected exact 'Discovery' first, got {results[0]['name']!r}"
    )


def test_find_entities_view_and_scan_agree_on_top_hit(client):
    """When the view exists, the view-seeded ranking must keep the same top hit
    as the scan it replaces for exact and prefix matches."""
    if not client._view_available():
        pytest.skip("no entities_v2_search view in this database")
    for needle in ("Discovery", "incident", "sys_user"):
        scan = client._db.aql.execute(
            client._find_entities_query(False),
            bind_vars=client._find_entities_bind_vars(needle, 3, False),
        )
        view = client.find_entities(needle, limit=3)
        scan_rows = list(scan)
        if not scan_rows or not view:
            continue
        assert view[0]["id"] == scan_rows[0]["id"], needle


def test_get_entity_returns_full_doc_or_none(client):
    # Pick any entity from find_entities to use as a known-good ID
    found = client.find_entities("incident", limit=1)
    if not found:
        pytest.skip("no entities matching 'incident' in this KG")
    entity = client.get_entity(found[0]["id"])
    assert entity is not None
    assert entity["id"] == found[0]["id"]
    assert "name" in entity


def test_get_entity_returns_none_for_missing(client):
    assert client.get_entity("entities_v2/does-not-exist-zzzz") is None


def test_neighbors_returns_nodes_and_edges(client):
    found = client.find_entities("management", limit=1)
    if not found:
        pytest.skip("no entities matching 'management' in this KG")
    result = client.neighbors(found[0]["id"], depth=1)
    assert "nodes" in result
    assert "edges" in result
    assert isinstance(result["nodes"], list)
    assert isinstance(result["edges"], list)


def test_neighbors_edges_carry_provenance(client):
    """Edges must surface source_document + source_quote (verbatim provenance),
    with the quote truncated to <=240 chars to bound response size."""
    found = client.find_entities("management", limit=1)
    if not found:
        pytest.skip("no entities matching 'management' in this KG")
    result = client.neighbors(found[0]["id"], depth=1)
    if not result["edges"]:
        pytest.skip("entity has no edges in this KG")
    quoted = 0
    for e in result["edges"]:
        assert "source_document" in e
        assert "source_quote" in e
        # reliability + version signals (added v0.3.5)
        assert "extraction_type" in e
        assert "releases" in e
        if e["source_quote"]:
            assert len(e["source_quote"]) <= 240
            quoted += 1
    # ~99.99% of relationships_v2 edges carry a quote -> at least one here
    assert quoted > 0, "expected at least one edge with a non-empty source_quote"
    # nodes carry per-entity version context
    for n in result["nodes"]:
        assert "releases" in n


def test_neighbors_edges_confidence_ranked(client):
    """Edges come back highest-confidence first so a truncated cap keeps the
    most-reliable edges (null confidences sort last)."""
    found = client.find_entities("management", limit=1)
    if not found:
        pytest.skip("no entities matching 'management' in this KG")
    result = client.neighbors(found[0]["id"], depth=1, limit=50)
    confs = [e["confidence"] for e in result["edges"] if e["confidence"] is not None]
    assert confs == sorted(confs, reverse=True), "edges not confidence-descending"


def test_neighbors_tie_break_diversifies_predicates_on_a_hub(client):
    """Within one (confidence, provenance) tie band the cap must not be one predicate's slice."""
    found = client.find_entities("admin", limit=1)
    if not found or int(found[0].get("degree") or 0) < 1000:
        pytest.skip("no hub-sized 'admin' entity in this KG")
    result = client.neighbors(found[0]["id"], depth=1, limit=100)
    edges = result["edges"]
    if len(edges) < 100:
        pytest.skip("hub smaller than the row cap")
    confs = [e["confidence"] for e in edges if e["confidence"] is not None]
    assert confs == sorted(confs, reverse=True)
    band = [e for e in edges if e["confidence"] == edges[0]["confidence"]]
    assert len({e["predicate"] for e in band}) >= 2, "top band is a single-predicate slice"


def test_schema_returns_entity_and_predicate_lists(client):
    schema = client.schema()
    assert "entity_types" in schema
    assert "predicates" in schema
    assert isinstance(schema["entity_types"], list)
    assert isinstance(schema["predicates"], list)


def test_can_read_collection_denies_out_of_scope(client):
    """Isolation check — the scoped user must NOT see collections outside the KG."""
    # The scoped user should be denied on any collection not explicitly granted.
    assert client.can_read_collection("some_other_collection") is False


def test_find_entities_returns_degree(kg_client):
    kg_client._db.aql.execute.return_value = iter(
        [
            {
                "id": "entities_v2/x",
                "name": "X",
                "type": "Feature",
                "source_documents": [],
                "releases": None,
                "degree": 42,
            },
        ]
    )
    out = kg_client.find_entities("X")
    assert out[0]["degree"] == 42
    aql = kg_client._db.aql.execute.call_args.args[0]
    assert "degree: degree" in aql  # RETURN now exposes the computed degree


def test_neighbors_stratified_query_shape(kg_client):
    kg_client._db.aql.execute.return_value = iter(
        [
            {
                "vertex": {"id": "entities_v2/n", "name": "n", "type": "T", "releases": None},
                "edge": {
                    "id": "1",
                    "source": "entities_v2/x",
                    "target": "entities_v2/n",
                    "predicate": "CONTAINS",
                    "confidence": None,
                    "extraction_type": "explicit",
                    "releases": None,
                    "source_document": "d",
                    "source_quote": "q",
                },
                "fetched": 120,
            },
        ]
    )
    out = kg_client.neighbors_stratified("entities_v2/x", per_predicate=2, overall=30)
    assert set(out) == {"nodes", "edges", "fetched"}
    assert out["edges"][0]["confidence"] is None  # null preserved in OUTPUT
    aql = kg_client._db.aql.execute.call_args.args[0]
    assert "COLLECT" in aql and "0.5" in aql  # stratification + null coalesce in ORDERING


def test_neighbors_stratified_pool_cap_and_bindvar_wiring(kg_client):
    kg_client._db.aql.execute.return_value = iter([])
    kg_client.neighbors_stratified("entities_v2/x", per_predicate=4, overall=33)
    aql = kg_client._db.aql.execute.call_args.args[0]
    bind = kg_client._db.aql.execute.call_args.kwargs["bind_vars"]
    assert "LIMIT 5000" in aql  # hub-memory safety cap (spec)
    assert bind["pp"] == 4  # per_predicate wired to @pp
    assert bind["overall"] == 33  # overall wired to @overall


def test_neighbors_stratified_predicates_upper_normalized(kg_client):
    kg_client._db.aql.execute.return_value = iter([])
    kg_client.neighbors_stratified("entities_v2/x", predicates=["contains"])
    bind = kg_client._db.aql.execute.call_args.kwargs["bind_vars"]
    assert bind["preds"] == ["CONTAINS"]


def test_count_edges_uses_count_aggregate(kg_client):
    kg_client._db.aql.execute.return_value = iter([57])
    assert kg_client.count_edges("entities_v2/x", predicates=["CONTAINS"]) == 57


def _stub_rows(kg_client, rows):
    kg_client._db.aql.execute.return_value = iter(rows)


def test_find_entities_projects_updated_at(kg_client):
    _stub_rows(
        kg_client,
        [
            {
                "id": "entities_v2/a",
                "name": "Discovery",
                "type": "product",
                "source_documents": [],
                "releases": None,
                "degree": 3,
                "updated_at": "2026-06-04T00:00:00Z",
            }
        ],
    )
    out = kg_client.find_entities("Discovery", limit=1)
    aql = kg_client._db.aql.execute.call_args[0][0]
    assert "updated_at: e.updated_at" in aql
    assert out[0]["updated_at"] == "2026-06-04T00:00:00Z"


def test_neighbors_projects_updated_at_on_nodes_and_edges(kg_client):
    _stub_rows(
        kg_client,
        [
            {
                "vertex": {
                    "id": "entities_v2/a",
                    "name": "A",
                    "type": "product",
                    "releases": None,
                    "updated_at": "2026-06-04T00:00:00Z",
                },
                "edge": {
                    "id": "e1",
                    "source": "entities_v2/a",
                    "target": "entities_v2/b",
                    "predicate": "CONTAINS",
                    "confidence": 0.9,
                    "extraction_type": "explicit",
                    "releases": None,
                    "source_document": "ITSM",
                    "source_quote": "q",
                    "updated_at": None,
                },
            }
        ],
    )
    out = kg_client.neighbors("entities_v2/a")
    aql = kg_client._db.aql.execute.call_args[0][0]
    assert "updated_at: v.updated_at" in aql  # vertex projection
    assert "updated_at: e.updated_at" in aql  # edge projection
    assert out["nodes"][0]["updated_at"] == "2026-06-04T00:00:00Z"
    assert out["edges"][0]["updated_at"] is None


def test_neighbors_stratified_projects_updated_at(kg_client):
    _stub_rows(
        kg_client,
        [
            {
                "vertex": {
                    "id": "entities_v2/a",
                    "name": "A",
                    "type": "product",
                    "releases": None,
                    "updated_at": None,
                },
                "edge": {
                    "id": "e1",
                    "source": "entities_v2/a",
                    "target": "entities_v2/b",
                    "predicate": "CONTAINS",
                    "confidence": 0.9,
                    "extraction_type": "explicit",
                    "releases": None,
                    "source_document": "ITSM",
                    "source_quote": "q",
                    "updated_at": "2026-07-01T00:00:00Z",
                },
                "fetched": 1,
            }
        ],
    )
    out = kg_client.neighbors_stratified("entities_v2/a")
    aql = kg_client._db.aql.execute.call_args[0][0]
    assert "updated_at: v.updated_at" in aql  # vertex projection
    assert "updated_at: e.updated_at" in aql  # edge projection
    assert out["edges"][0]["updated_at"] == "2026-07-01T00:00:00Z"


# --- find_entities: view-seeded with scan fallback (spec §6 Track 1) ---------


class _ViewsOnlyDB:
    """Stand-in for the real python-arango driver (8.3.5): exposes ``views()`` and
    ``aql`` but deliberately NOT ``has_view`` — unlike a plain ``MagicMock``, which
    auto-vivifies any attribute and so never exercises the ``AttributeError`` ->
    ``views()`` fallback in ``_view_available``. This is the actual code path a real
    database hits, since python-arango has no ``has_view`` method to begin with.
    """

    def __init__(self, view_names=None, views_error=None):
        self._view_names = view_names or []
        self._views_error = views_error
        self.views_call_count = 0
        self.aql = MagicMock()

    def views(self):
        self.views_call_count += 1
        if self._views_error is not None:
            raise self._views_error
        return [{"name": n} for n in self._view_names]


def _row(name="X", degree=1):
    return {
        "id": f"entities_v2/{name.lower()}",
        "name": name,
        "type": "Feature",
        "source_documents": [],
        "releases": None,
        "updated_at": None,
        "degree": degree,
    }


def test_find_entities_uses_the_search_view_when_present(kg_client):
    kg_client._db.has_view.return_value = True
    kg_client._db.aql.execute.return_value = iter([_row()])
    kg_client.find_entities("sys_user", limit=5)
    aql = kg_client._db.aql.execute.call_args.args[0]
    bind = kg_client._db.aql.execute.call_args.kwargs["bind_vars"]
    assert "FOR c IN entities_v2_search" in aql
    assert "test_kg::norm_en" in aql  # analyzer is database-scoped
    assert 'TOKENS(@needle, "text_en")' in aql
    assert "LIMIT @cand_cap" in aql and "LIMIT @limit" in aql
    assert "degree: degree" in aql
    assert bind["cand_cap"] == 200 and bind["limit"] == 5
    assert bind["needle"] == "sys_user" and bind["needle_lc"] == "sys_user"
    assert bind["needle_like"] == "%sys\\_user%"  # `_` is a LIKE wildcard; escaped


def test_find_entities_falls_back_to_scan_when_view_absent(kg_client):
    kg_client._db.has_view.return_value = False
    kg_client._db.aql.execute.return_value = iter([_row()])
    kg_client.find_entities("Incident")
    aql = kg_client._db.aql.execute.call_args.args[0]
    bind = kg_client._db.aql.execute.call_args.kwargs["bind_vars"]
    assert "FILTER CONTAINS(LOWER(e.name), @needle_lc)" in aql
    assert "entities_v2_search" not in aql
    assert set(bind) == {"needle_lc", "limit", "graph"}  # unused bind vars are an AQL error
    assert bind["needle_lc"] == "incident"


def test_find_entities_probes_the_view_once(kg_client):
    kg_client._db.has_view.return_value = True
    kg_client._db.aql.execute.return_value = iter([])
    kg_client.find_entities("a")
    kg_client._db.aql.execute.return_value = iter([])
    kg_client.find_entities("b")
    assert kg_client._db.has_view.call_count == 1


def test_find_entities_probe_failure_means_scan(kg_client):
    from arango.exceptions import ArangoError as DriverError

    kg_client._db.has_view.side_effect = DriverError("403")
    kg_client._db.aql.execute.return_value = iter([])
    kg_client.find_entities("a")
    aql = kg_client._db.aql.execute.call_args.args[0]
    assert "FOR e IN entities_v2" in aql


# --- find_entities: the real driver has no has_view() (production path) -----


def test_find_entities_uses_the_view_via_the_views_fallback(kg_client):
    """python-arango 8.3.5 has no has_view(); this is the actual path a real
    database takes, unlike the MagicMock-based tests above where has_view is
    auto-vivified and the AttributeError branch is never hit."""
    kg_client._db = _ViewsOnlyDB(view_names=["entities_v2_search"])
    kg_client._db.aql.execute.return_value = iter([_row()])
    kg_client.find_entities("sys_user", limit=5)
    aql = kg_client._db.aql.execute.call_args.args[0]
    assert "FOR c IN entities_v2_search" in aql
    assert kg_client._db.views_call_count == 1


def test_find_entities_views_fallback_scans_when_view_absent(kg_client):
    kg_client._db = _ViewsOnlyDB(view_names=["some_other_view"])
    kg_client._db.aql.execute.return_value = iter([_row()])
    kg_client.find_entities("Incident")
    aql = kg_client._db.aql.execute.call_args.args[0]
    assert "FOR e IN entities_v2" in aql
    assert "entities_v2_search" not in aql


def test_find_entities_views_fallback_probe_error_means_scan(kg_client):
    from arango.exceptions import ArangoError as DriverError

    kg_client._db = _ViewsOnlyDB(views_error=DriverError("403"))
    kg_client._db.aql.execute.return_value = iter([])
    kg_client.find_entities("a")
    aql = kg_client._db.aql.execute.call_args.args[0]
    assert "FOR e IN entities_v2" in aql
    # the probe failure is still cached — a second call must not re-probe
    kg_client._db.aql.execute.return_value = iter([])
    kg_client.find_entities("b")
    assert kg_client._db.views_call_count == 1


def test_like_pattern_escapes_wildcards():
    from arango_client import ArangoKGClient

    assert ArangoKGClient._like_pattern("a%b_c\\d") == "%a\\%b\\_c\\\\d%"


# --- shortest_path: release suppression + hub abstention (spec §6, round A) ----


def _v(key, vtype="Table", name=None):
    return {"_id": f"entities_v2/{key}", "name": name or key, "type": vtype, "releases": None}


def _e(a, b, pred="CONTAINS"):
    return {
        "_from": f"entities_v2/{a}",
        "_to": f"entities_v2/{b}",
        "predicate": pred,
        "extraction_type": "explicit",
        "releases": None,
        "source_document": "d",
        "source_quote": "q" * 300,
    }


def _path(*keys_and_types):
    verts = [_v(k, t) for k, t in keys_and_types]
    edges = [_e(keys_and_types[i][0], keys_and_types[i + 1][0]) for i in range(len(verts) - 1)]
    return {"vertices": verts, "edges": edges}


def _degrees(**by_key):
    return iter([{"id": f"entities_v2/{k}", "degree": d} for k, d in by_key.items()])


def test_shortest_path_uses_k_shortest_paths_with_a_candidate_cap(kg_client):
    kg_client._db.aql.execute.side_effect = [
        iter([_path(("a", "Table"), ("m", "Plugin"), ("b", "Role"))]),
        _degrees(m=5),
    ]
    out = kg_client.shortest_path("entities_v2/a", "entities_v2/b", max_hops=4)
    first_aql = kg_client._db.aql.execute.call_args_list[0].args[0]
    first_bind = kg_client._db.aql.execute.call_args_list[0].kwargs["bind_vars"]
    assert "K_SHORTEST_PATHS @from TO @to GRAPH @graph" in first_aql
    assert first_bind["cap"] == 20
    assert [n["id"] for n in out["nodes"]] == ["entities_v2/a", "entities_v2/m", "entities_v2/b"]
    assert (
        out["edges"][0]["source"] == "entities_v2/a" and len(out["edges"][0]["source_quote"]) == 240
    )
    assert "abstained" not in out


def test_shortest_path_skips_release_mediated_candidate_for_next_clean_one(kg_client):
    kg_client._db.aql.execute.side_effect = [
        iter(
            [
                _path(("a", "Table"), ("zurich", "Release"), ("b", "Role")),
                _path(("a", "Table"), ("m", "Plugin"), ("n", "Feature"), ("b", "Role")),
            ]
        ),
        _degrees(m=3, n=7),
    ]
    out = kg_client.shortest_path("entities_v2/a", "entities_v2/b", max_hops=4)
    assert [n["id"] for n in out["nodes"]][1] == "entities_v2/m"
    # the degree lookup covers only the surviving candidates' intermediates
    ids = kg_client._db.aql.execute.call_args_list[1].kwargs["bind_vars"]["ids"]
    assert ids == ["entities_v2/m", "entities_v2/n"]


def test_shortest_path_abstains_when_every_candidate_crosses_a_hub(kg_client):
    kg_client._db.aql.execute.side_effect = [
        iter(
            [
                _path(("a", "Table"), ("admin", "Role"), ("b", "Feature")),
                _path(("a", "Table"), ("admin", "Role"), ("x", "Plugin"), ("b", "Feature")),
            ]
        ),
        _degrees(admin=26602, x=4),
    ]
    out = kg_client.shortest_path("entities_v2/a", "entities_v2/b", max_hops=4)
    assert out["abstained"] is True and out["nodes"] == [] and out["edges"] == []
    assert "degree 1000" in out["reason"] and "2 candidate" in out["reason"]
    assert out["hubs"] == [
        {"id": "entities_v2/admin", "name": "admin", "type": "Role", "degree": 26602}
    ]


def test_shortest_path_abstains_when_all_candidates_are_release_mediated(kg_client):
    kg_client._db.aql.execute.side_effect = [
        iter([_path(("a", "Table"), ("zurich", "Release"), ("b", "Role"))]),
    ]
    out = kg_client.shortest_path("entities_v2/a", "entities_v2/b", max_hops=4)
    assert out["abstained"] is True
    assert "1 release-mediated" in out["reason"] and out["hubs"] == []
    assert kg_client._db.aql.execute.call_count == 1  # no degree query needed


def test_shortest_path_ignores_candidates_beyond_max_hops(kg_client):
    kg_client._db.aql.execute.side_effect = [
        iter([_path(("a", "Table"), ("m", "Plugin"), ("n", "Feature"), ("b", "Role"))]),
    ]
    assert kg_client.shortest_path("entities_v2/a", "entities_v2/b", max_hops=2) is None


def test_shortest_path_endpoint_hubs_do_not_trigger_abstention(kg_client):
    kg_client._db.aql.execute.side_effect = [
        iter([_path(("admin", "Role"), ("m", "Plugin"), ("b", "Feature"))]),
        _degrees(m=2),
    ]
    out = kg_client.shortest_path("entities_v2/admin", "entities_v2/b")
    assert "abstained" not in out and out["nodes"][0]["id"] == "entities_v2/admin"


def test_shortest_path_release_endpoint_is_allowed(kg_client):
    kg_client._db.aql.execute.side_effect = [
        iter([_path(("a", "Feature"), ("zurich", "Release"))]),
        iter([]),
    ]
    out = kg_client.shortest_path("entities_v2/a", "entities_v2/zurich")
    assert out["nodes"][-1]["type"] == "Release" and "abstained" not in out


# --- neighbors: tie-break within the confidence band (spec §6; arch report K) ------


def test_neighbors_sorts_by_confidence_then_provenance_then_predicate_rank(kg_client):
    kg_client._db.aql.execute.return_value = iter([])
    kg_client.neighbors("entities_v2/x", depth=1, limit=50)
    aql = kg_client._db.aql.execute.call_args.args[0]
    bind = kg_client._db.aql.execute.call_args.kwargs["bind_vars"]
    # v2 has no provenance array yet; LENGTH(releases) is the stand-in (see PROVENANCE_COUNT_AQL)
    assert "SORT e.confidence DESC, LENGTH(e.releases || []) DESC" in aql
    assert "COLLECT p = r.edge.predicate INTO grp" in aql
    assert "pred_rank" in aql
    assert "SORT r.edge.confidence DESC, LENGTH(r.edge.releases || []) DESC, r.pred_rank ASC" in aql
    assert aql.index("pred_rank ASC") < aql.index("LIMIT @row_cap")  # tie-break BEFORE the cap
    assert bind["row_cap"] == 50


def test_neighbors_keeps_the_type_filter_inside_the_pool(kg_client):
    kg_client._db.aql.execute.return_value = iter([])
    kg_client.neighbors("entities_v2/x", types=["REQUIRES_ROLE"])
    aql = kg_client._db.aql.execute.call_args.args[0]
    assert aql.index("FILTER e.predicate IN @types") < aql.index("SORT e.confidence DESC")


def test_neighbors_pool_is_capped(kg_client):
    """A depth>1 hub traversal must stay bounded — the pool cap runs after the
    confidence/provenance SORT (keeps the top band) and before the per-predicate
    COLLECT/rank, so it applies to every depth, not just depth=1."""
    kg_client._db.aql.execute.return_value = iter([])
    kg_client.neighbors("entities_v2/x", depth=1, limit=50)
    aql = kg_client._db.aql.execute.call_args.args[0]
    bind = kg_client._db.aql.execute.call_args.kwargs["bind_vars"]
    assert aql.index("LIMIT @pool_cap") < aql.index("COLLECT p = r.edge.predicate INTO grp")
    assert aql.index("SORT e.confidence DESC") < aql.index("LIMIT @pool_cap")
    assert bind["pool_cap"] == NEIGHBORS_POOL_CAP == 5000


def test_neighbors_depth_2_sort_rank_path_interleaves_predicates(kg_client):
    """depth=2 must go through the same pool-cap -> COLLECT/rank -> final-sort path
    as depth=1: the AQL still carries 1..@depth and the pool cap bind var, and rows
    already ranked (by confidence, provenance, pred_rank) across two traversal
    depths come back in that same interleaved order — neighbors() does no
    additional in-Python resort that could undo the AQL's tie-break."""

    def _row(edge_id, predicate, confidence, releases):
        return {
            "vertex": {
                "id": f"entities_v2/{edge_id}-target",
                "name": edge_id,
                "type": "T",
                "releases": None,
                "updated_at": None,
            },
            "edge": {
                "id": edge_id,
                "source": "entities_v2/x",
                "target": f"entities_v2/{edge_id}-target",
                "predicate": predicate,
                "confidence": confidence,
                "extraction_type": "explicit",
                "releases": releases,
                "source_document": "d",
                "source_quote": "q",
                "updated_at": None,
            },
        }

    # Rows as the AQL would return them post-rank: tied confidence/provenance band
    # interleaved by predicate rank (A0, B0, A1, B1), one pair reached at 1-hop, the
    # other at 2-hops — depth is irrelevant to the sort/rank path itself.
    rows = [
        _row("a0", "CONTAINS", 0.9, ["zurich"]),  # 1 hop
        _row("b0", "USES_TABLE", 0.9, ["zurich"]),  # 2 hops
        _row("a1", "CONTAINS", 0.9, ["zurich"]),  # 2 hops
        _row("b1", "USES_TABLE", 0.9, ["zurich"]),  # 1 hop
    ]
    kg_client._db.aql.execute.return_value = iter(rows)
    out = kg_client.neighbors("entities_v2/x", depth=2, limit=50)
    aql = kg_client._db.aql.execute.call_args.args[0]
    bind = kg_client._db.aql.execute.call_args.kwargs["bind_vars"]
    assert "1..@depth" in aql
    assert bind["depth"] == 2
    assert bind["pool_cap"] == NEIGHBORS_POOL_CAP
    assert [e["id"] for e in out["edges"]] == ["a0", "b0", "a1", "b1"]
    assert [e["predicate"] for e in out["edges"]] == [
        "CONTAINS",
        "USES_TABLE",
        "CONTAINS",
        "USES_TABLE",
    ]


def test_neighbors_row_shape_and_fetched_unchanged(kg_client):
    kg_client._db.aql.execute.return_value = iter(
        [
            {
                "vertex": {
                    "id": "entities_v2/n",
                    "name": "n",
                    "type": "T",
                    "releases": None,
                    "updated_at": None,
                },
                "edge": {
                    "id": "1",
                    "source": "entities_v2/x",
                    "target": "entities_v2/n",
                    "predicate": "CONTAINS",
                    "confidence": 0.9,
                    "extraction_type": "explicit",
                    "releases": ["zurich"],
                    "source_document": "d",
                    "source_quote": "q",
                    "updated_at": None,
                },
            }
        ]
    )
    out = kg_client.neighbors("entities_v2/x")
    assert set(out) == {"nodes", "edges", "fetched"} and out["fetched"] == 1
    assert "pred_rank" not in out["edges"][0]
