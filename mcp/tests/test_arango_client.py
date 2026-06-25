"""Tests for the scoped ArangoDB client.

These tests use python-arango against a real Arango instance — they're
integration tests that require ARANGO_TEST_URL + ARANGO_TEST_USER +
ARANGO_TEST_PASSWORD in the env. Skipped if not provided.
"""

import os

import pytest
from arango_client import ArangoKGClient

pytestmark = pytest.mark.skipif(
    not os.environ.get("ARANGO_TEST_PASSWORD"),
    reason="set ARANGO_TEST_PASSWORD (and optional ARANGO_TEST_URL/USER) to run",
)


@pytest.fixture
def client():
    return ArangoKGClient(
        url=os.environ.get("ARANGO_TEST_URL", "http://localhost:8529"),
        database="knowledge_graph",
        username=os.environ.get("ARANGO_TEST_USER", "arango_reader"),
        password=os.environ["ARANGO_TEST_PASSWORD"],
    )


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
