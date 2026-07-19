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
from arango_client import ArangoKGClient


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
