import pytest

from embeddington import errors
from embeddington.format import records


def test_header_roundtrip():
    h = records.header(
        schema_version="1.0",
        prev_sha="a1b2",
        head_sha="c3d4",
        points=2,
        entities=3,
        edges=4,
    )
    line = records.encode(h)
    back = records.decode(line)
    assert back == h
    assert records.is_header(back)
    assert back["_hdr"]["head_sha"] == "c3d4"


def test_point_upsert_roundtrip():
    rec = records.point_upsert(
        point_id="p1", vector=[0.1, 0.2], payload={"filename": "a.md"}
    )
    back = records.decode(records.encode(rec))
    assert back["op"] == "upsert" and back["kind"] == "point"
    assert back["id"] == "p1" and back["vector"] == [0.1, 0.2]
    assert not records.is_header(back)


def test_entity_and_edge_upsert():
    e = records.entity_upsert(key="E1", doc={"name": "CMDB", "type": "Feature"})
    assert e["_key"] == "E1" and e["doc"]["type"] == "Feature"
    edge = records.edge_upsert(
        key="R1",
        from_="entities_v2/E1",
        to="entities_v2/E2",
        predicate="DEPENDS_ON",
        doc={"source_document": "a.md"},
    )
    assert edge["_from"] == "entities_v2/E1" and edge["predicate"] == "DEPENDS_ON"


def test_delete_builders():
    assert records.point_delete_by_filename("gone.md") == {
        "op": "delete",
        "kind": "point",
        "filename": "gone.md",
    }
    assert records.entity_delete("E9") == {
        "op": "delete",
        "kind": "entity",
        "_key": "E9",
    }
    assert records.edge_delete("R9") == {"op": "delete", "kind": "edge", "_key": "R9"}


def test_decode_rejects_unknown_op():
    with pytest.raises(errors.RecordError):
        records.decode('{"op": "frobnicate", "kind": "point"}')


def test_decode_rejects_unknown_kind():
    with pytest.raises(errors.RecordError):
        records.decode('{"op": "upsert", "kind": "sasquatch"}')


def test_decode_rejects_invalid_json():
    with pytest.raises(errors.RecordError):
        records.decode("{not json")
