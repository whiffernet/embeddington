import pytest

from embeddington import errors
from embeddington.apply import apply_diff
from embeddington.format import records


def _bundle():
    return [
        records.header("1.0", "a1", "b2", points=2, entities=1, edges=1),
        records.point_upsert("p1", [0.1], {"filename": "a.md"}),
        records.point_upsert("p2", [0.2], {"filename": "b.md"}),
        records.entity_upsert("E1", {"name": "CMDB"}),
        records.edge_upsert("R1", "entities_v2/E1", "entities_v2/E2", "USES", {}),
        records.point_delete_by_filename("old.md"),
        records.entity_delete("E_gone"),
    ]


def test_apply_writes_all_records(fake_qdrant, fake_arango):
    result = apply_diff.apply_diff(_bundle(), fake_qdrant, fake_arango)
    assert set(fake_qdrant.points) == {"p1", "p2"}
    assert fake_arango.entities == {"E1": {"name": "CMDB"}}
    assert "R1" in fake_arango.edges
    assert result["counts"] == {"points": 2, "entities": 1, "edges": 1, "deletes": 2}
    assert result["header"]["head_sha"] == "b2"


def test_apply_processes_tombstones(fake_qdrant, fake_arango):
    fake_qdrant.upsert_point("px", [0.0], {"filename": "old.md"})
    fake_arango.upsert_entity("E_gone", {"name": "obsolete"})
    apply_diff.apply_diff(_bundle(), fake_qdrant, fake_arango)
    assert "px" not in fake_qdrant.points
    assert "E_gone" not in fake_arango.entities


def test_apply_deletes_point_by_id(fake_qdrant, fake_arango):
    fake_qdrant.upsert_point("px", [0.0], {"filename": "keep.md"})
    bundle = [{"op": "delete", "kind": "point", "id": "px"}]
    apply_diff.apply_diff(bundle, fake_qdrant, fake_arango)
    assert "px" not in fake_qdrant.points


def test_apply_rejects_bad_record(fake_qdrant, fake_arango):
    bad = [
        {"op": "upsert", "kind": "point"}
    ]  # missing id/vector/payload triggers KeyError->RecordError
    with pytest.raises(errors.RecordError):
        apply_diff.apply_diff(bad, fake_qdrant, fake_arango)


def test_apply_deletes_edge_by_key(fake_qdrant, fake_arango):
    fake_arango.upsert_edge(
        "R_old", "entities_v2/E1", "entities_v2/E2", {"predicate": "OLD"}
    )
    bundle = [
        records.header("1.0", "a1", "b2", points=0, entities=0, edges=0),
        records.edge_delete("R_old"),
    ]
    apply_diff.apply_diff(bundle, fake_qdrant, fake_arango)
    assert "R_old" not in fake_arango.edges
