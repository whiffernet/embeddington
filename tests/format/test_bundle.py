import pytest

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


def test_write_bundle_flushes_incrementally_on_failure(tmp_path):
    def gen():
        yield records.header("2.0.0", None, "aa", 1, 0, 0)
        yield records.point_upsert("p0", [0.1], {})
        raise RuntimeError("stop")

    path = tmp_path / "b.jsonl"  # plain (zstd frames would buffer)
    with pytest.raises(RuntimeError):
        bundle.write_bundle(path, gen())
    # Streaming impl: file opened + earlier records flushed on context close.
    # Old join-based impl: join raises BEFORE write_bytes -> file never created.
    assert path.exists() and path.read_text().strip() != ""


def test_bundle_streams_generators_roundtrip(tmp_path):
    def gen():
        yield records.header("2.0.0", None, "aaaa", 10_000, 0, 0)
        for i in range(10_000):
            yield records.point_upsert(f"p{i}", [0.1, 0.2], {"file_name": f"f{i}"})

    path = tmp_path / "big.jsonl.zst"
    bundle.write_bundle(path, gen())
    out = list(bundle.read_bundle(path))
    assert out[0]["_hdr"]["points"] == 10_000
    assert len(out) == 10_001 and out[-1]["id"] == "p9999"
