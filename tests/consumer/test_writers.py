from consumer import writers
from embeddington.apply import apply_diff
from embeddington.format import records


def test_qdrant_writer_upsert_and_delete(fake_qdrant_client):
    w = writers.QdrantConsumerWriter(fake_qdrant_client, collection="technology")
    w.upsert_point("p1", [0.1, 0.2], {"filename": "a.md"})
    assert fake_qdrant_client.points["p1"] == {
        "vector": [0.1, 0.2],
        "payload": {"filename": "a.md"},
    }
    w.delete_point("p1")
    assert "p1" not in fake_qdrant_client.points


def test_qdrant_writer_delete_by_filename(fake_qdrant_client):
    w = writers.QdrantConsumerWriter(fake_qdrant_client, collection="technology")
    w.upsert_point("p1", [0.1], {"filename": "a.md"})
    w.upsert_point("p2", [0.2], {"filename": "b.md"})
    w.delete_points_by_filename("a.md")
    assert set(fake_qdrant_client.points) == {"p2"}


def test_arango_writer_upsert_entity_and_edge_persists_predicate(fake_arango_db):
    w = writers.ArangoConsumerWriter(fake_arango_db)
    w.upsert_entity("E1", {"name": "CMDB", "type": "Feature"})
    assert fake_arango_db.collections["entities_v2"]["E1"]["name"] == "CMDB"
    w.upsert_edge(
        "R1",
        "entities_v2/E1",
        "entities_v2/E2",
        {"predicate": "USES", "source_document": "a.md"},
    )
    stored = fake_arango_db.collections["relationships_v2"]["R1"]
    assert stored["_from"] == "entities_v2/E1" and stored["_to"] == "entities_v2/E2"
    assert stored["predicate"] == "USES"  # I1: predicate persisted via doc


def test_apply_diff_through_real_writers(fake_qdrant_client, fake_arango_db):
    qw = writers.QdrantConsumerWriter(fake_qdrant_client, collection="technology")
    aw = writers.ArangoConsumerWriter(fake_arango_db)
    bundle = [
        records.header("1.0", "a1", "b2", points=1, entities=1, edges=1),
        records.point_upsert("p1", [0.1], {"filename": "a.md"}),
        records.entity_upsert("E1", {"name": "CMDB"}),
        records.edge_upsert(
            "R1", "entities_v2/E1", "entities_v2/E2", "USES", {"predicate": "USES"}
        ),
    ]
    apply_diff.apply_diff(bundle, qw, aw)
    assert "p1" in fake_qdrant_client.points
    assert "E1" in fake_arango_db.collections["entities_v2"]
    assert fake_arango_db.collections["relationships_v2"]["R1"]["predicate"] == "USES"


def test_point_count_returns_zero_when_collection_missing(fake_qdrant_client):
    fake_qdrant_client.exists = False
    qw = writers.QdrantConsumerWriter(fake_qdrant_client, "technology")
    assert qw.point_count() == 0


def test_point_count_returns_number_of_points(fake_qdrant_client):
    qw = writers.QdrantConsumerWriter(fake_qdrant_client, "technology")
    qw.upsert_point("p1", [0.1], {"filename": "a.md"})
    qw.upsert_point("p2", [0.2], {"filename": "b.md"})
    assert qw.point_count() == 2


def test_collection_property_exposes_name(fake_qdrant_client):
    assert writers.QdrantConsumerWriter(fake_qdrant_client, "technology").collection == "technology"


def test_entity_count_counts_entities(fake_arango_db):
    aw = writers.ArangoConsumerWriter(fake_arango_db)
    assert aw.entity_count() == 0
    aw.upsert_entity("e1", {"name": "ServiceNow"})
    assert aw.entity_count() == 1
