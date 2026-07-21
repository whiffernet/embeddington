import pytest
from arango.exceptions import ArangoServerError

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
    # Upsert first, then flip to "missing". If the collection_exists() guard in
    # point_count() were ever removed, the fake's count() would raise (matching the
    # real client) instead of coincidentally returning 0, so this pins the guard.
    qw = writers.QdrantConsumerWriter(fake_qdrant_client, "technology")
    qw.upsert_point("p1", [0.1], {"filename": "a.md"})
    qw.upsert_point("p2", [0.2], {"filename": "b.md"})
    fake_qdrant_client.exists = False
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


def test_entity_count_returns_zero_when_the_database_does_not_exist(fake_arango_db):
    """The fresh-install state: technology_kg is created BY arangorestore, not before it.

    python-arango's db.collection() is lazy, so the writer constructs fine, and count() then
    404s ("database not found"). Without the existence guard the updater's populated-store
    check dies with an unhandled DocumentCountError on the very first run.
    """
    aw = writers.ArangoConsumerWriter(fake_arango_db)  # handles built while the db "exists"
    fake_arango_db.db_exists = False
    assert aw.entity_count() == 0


def test_entity_count_returns_zero_when_the_collection_is_missing(fake_arango_db):
    """The database exists (an empty Arango) but entities_v2 has never been created."""
    aw = writers.ArangoConsumerWriter(fake_arango_db)
    del fake_arango_db.collections["entities_v2"]
    assert aw.entity_count() == 0


@pytest.mark.parametrize(
    "error_code,message,status_code",
    [
        (11, "not authorized to execute this request", 401),  # per-database ACL (Arango 3.12)
        (11, "forbidden", 403),
        (0, "internal server error", 500),
        (0, "service unavailable", 503),  # still replaying the WAL while Qdrant already serves
    ],
)
def test_entity_count_propagates_a_real_arango_failure(
    fake_arango_db, error_code, message, status_code
):
    """A LIVE-but-unhappy Arango must never be reported as an empty graph.

    python-arango raises CollectionListError / DocumentCountError on ANY non-success response,
    so a 401/403/500/503 arrives as the same class as a genuine 404. Swallowing those as 0 is
    strictly worse than crashing: the updater's guard reads "Qdrant full, Arango empty", takes
    that for an interrupted import, and re-restores 828 MB over a healthy store.
    """
    aw = writers.ArangoConsumerWriter(fake_arango_db)
    fake_arango_db.server_error = (error_code, message, status_code)

    with pytest.raises(ArangoServerError) as exc:
        aw.entity_count()
    assert exc.value.http_code == status_code


def test_create_collection_maps_config(fake_qdrant_client):
    w = writers.QdrantConsumerWriter(fake_qdrant_client, "technology")
    fake_qdrant_client.exists = False
    w.create_collection(size=1024, distance="Cosine", hnsw_m=16, hnsw_ef_construct=100)
    assert fake_qdrant_client.created == {
        "size": 1024,
        "distance": "Cosine",
        "m": 16,
        "ef_construct": 100,
    }
    fake_qdrant_client.exists = True
    w.create_collection(size=1024)  # no-op second call


def test_upsert_points_batches(fake_qdrant_client):
    w = writers.QdrantConsumerWriter(fake_qdrant_client, "technology")
    w.upsert_points(((f"p{i}", [0.1], {"k": i}) for i in range(600)), batch=256)
    assert [len(b) for b in fake_qdrant_client.upsert_batches] == [256, 256, 88]


def test_entity_count_propagates_a_failure_raised_by_the_count_itself(fake_arango_db):
    """The second call site: has_collection() succeeds, then count() 500s.

    Guards the other half of the try-block -- a discriminator applied to only one of the two
    exception types would leave this path swallowing errors as 0.
    """
    aw = writers.ArangoConsumerWriter(fake_arango_db)
    original_has_collection = fake_arango_db.has_collection

    def has_collection(name):
        result = original_has_collection(name)
        fake_arango_db.server_error = (0, "internal server error", 500)  # dies at count() only
        return result

    fake_arango_db.has_collection = has_collection

    with pytest.raises(ArangoServerError) as exc:
        aw.entity_count()
    assert exc.value.http_code == 500
