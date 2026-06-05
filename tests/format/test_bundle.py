from embeddington.format import bundle, records


def _sample_records():
    return [
        records.header("1.0", "a1", "b2", points=1, entities=1, edges=1),
        records.point_upsert("p1", [0.1, 0.2], {"filename": "a.md"}),
        records.entity_upsert("E1", {"name": "CMDB"}),
        records.edge_upsert("R1", "entities_v2/E1", "entities_v2/E2", "USES", {}),
    ]


def test_plain_jsonl_roundtrip(tmp_path):
    path = tmp_path / "diff-b2.jsonl"
    bundle.write_bundle(path, _sample_records())
    assert list(bundle.read_bundle(path)) == _sample_records()


def test_zstd_roundtrip(tmp_path):
    path = tmp_path / "diff-b2.jsonl.zst"
    bundle.write_bundle(path, _sample_records())
    assert path.read_bytes()[:4] == b"\x28\xb5\x2f\xfd"  # zstd magic number
    assert list(bundle.read_bundle(path)) == _sample_records()


def test_read_is_lazy_iterator(tmp_path):
    path = tmp_path / "diff.jsonl"
    bundle.write_bundle(path, _sample_records())
    it = bundle.read_bundle(path)
    assert next(it)["_hdr"]["head_sha"] == "b2"
