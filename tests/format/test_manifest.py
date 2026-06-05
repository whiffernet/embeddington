import pytest

from embeddington import errors
from embeddington.format import manifest


def _good_manifest():
    return {
        "schema_version": "1.0",
        "baselines": [
            {
                "tag": "baseline-2026-06",
                "head_sha": "c3d4",
                "points": 62717,
                "entities": 10,
                "edges": 20,
                "assets": {"qdrant": "q.snapshot.zst", "arango": "a.dump.zst"},
                "sha256": {"qdrant": "aa", "arango": "bb"},
            }
        ],
        "diffs": [
            {
                "prev_sha": "c3d4",
                "head_sha": "e5f6",
                "asset": "diff-e5f6.jsonl.zst",
                "sha256": "cc",
            }
        ],
    }


def test_validate_accepts_good_manifest():
    manifest.validate_manifest(_good_manifest())  # no raise


def test_validate_rejects_missing_schema_version():
    m = _good_manifest()
    del m["schema_version"]
    with pytest.raises(errors.ManifestError):
        manifest.validate_manifest(m)


def test_validate_rejects_no_baselines():
    m = _good_manifest()
    m["baselines"] = []
    with pytest.raises(errors.ManifestError):
        manifest.validate_manifest(m)


def test_validate_rejects_malformed_baseline():
    m = _good_manifest()
    del m["baselines"][0]["head_sha"]
    with pytest.raises(errors.ManifestError):
        manifest.validate_manifest(m)


def test_load_and_dump_roundtrip(tmp_path):
    path = tmp_path / "manifest.json"
    manifest.dump_manifest(_good_manifest(), path)
    loaded = manifest.load_manifest(path)
    assert loaded == _good_manifest()


def test_sha256_file_and_verify(tmp_path):
    f = tmp_path / "asset.bin"
    f.write_bytes(b"hello embeddington")
    digest = manifest.sha256_file(f)
    assert len(digest) == 64
    manifest.verify_asset(f, digest)  # no raise
    with pytest.raises(errors.ChecksumError):
        manifest.verify_asset(f, "0" * 64)
