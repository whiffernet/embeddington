def test_fake_qdrant_upsert_is_idempotent(fake_qdrant):
    fake_qdrant.upsert_point("p1", [0.1], {"filename": "a.md"})
    fake_qdrant.upsert_point("p1", [0.9], {"filename": "a.md"})  # overwrite
    assert len(fake_qdrant.points) == 1
    assert fake_qdrant.points["p1"]["vector"] == [0.9]


def test_fake_qdrant_delete_by_filename(fake_qdrant):
    fake_qdrant.upsert_point("p1", [0.1], {"filename": "a.md"})
    fake_qdrant.upsert_point("p2", [0.2], {"filename": "b.md"})
    fake_qdrant.delete_points_by_filename("a.md")
    assert set(fake_qdrant.points) == {"p2"}
    fake_qdrant.delete_points_by_filename("nope.md")  # no error when absent


def test_fake_arango_entity_and_edge(fake_arango):
    fake_arango.upsert_entity("E1", {"name": "CMDB"})
    fake_arango.upsert_entity("E1", {"name": "CMDB2"})  # overwrite
    assert fake_arango.entities["E1"]["name"] == "CMDB2"
    fake_arango.upsert_edge("R1", "entities_v2/E1", "entities_v2/E2", {"predicate": "USES"})
    fake_arango.delete_edge("R1")
    fake_arango.delete_edge("R1")  # idempotent, no error
    assert fake_arango.edges == {}
