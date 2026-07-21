"""Tests for consumer.restore_ops — decompress logic + baseline-importer wiring."""

import tarfile
from pathlib import Path

import zstandard

from consumer import restore_ops


def _zst(path, data):
    path.write_bytes(zstandard.ZstdCompressor().compress(data))


def test_decompress_plain_zst(tmp_path):
    src = tmp_path / "technology.snapshot.zst"
    _zst(src, b"snapshot-bytes")
    out = restore_ops.decompress(src)
    assert out.endswith("technology.snapshot")
    assert Path(out).read_bytes() == b"snapshot-bytes"


def test_decompress_tar_zst_returns_dump_dir(tmp_path):
    # build a dump dir with one nested dir holding a file, tar+zstd it
    inner = tmp_path / "src" / "dump"
    inner.mkdir(parents=True)
    (inner / "entities.data.json").write_text("{}")
    tar_path = tmp_path / "arango-dump.tar"
    with tarfile.open(tar_path, "w") as tf:
        tf.add(inner, arcname="dump")
    archive = tmp_path / "arango-dump.tar.zst"
    _zst(archive, tar_path.read_bytes())

    out = Path(restore_ops.decompress(archive))
    assert out.is_dir()
    assert (out / "entities.data.json").exists()  # unwrapped the single inner dir


def test_restore_qdrant_bundle_creates_then_streams(tmp_path, monkeypatch):
    from embeddington.format import bundle, records

    path = tmp_path / "base.jsonl.zst"
    bundle.write_bundle(
        path,
        [
            records.header("2.0.0", None, "aa", 2, 0, 0),
            records.point_upsert("p1", [0.1], {"file_name": "f"}),
            records.point_upsert("p2", [0.2], {"file_name": "g"}),
        ],
    )
    calls = []

    class W:
        def create_collection(self, **cfg):
            calls.append(("create", cfg))

        def upsert_points(self, pts, batch=256):
            calls.append(("upsert", [p[0] for p in pts]))

    monkeypatch.setattr(
        restore_ops.writers.QdrantConsumerWriter,
        "connect",
        classmethod(lambda cls, url, coll: W()),
    )
    restore_ops.restore_qdrant_bundle(
        "http://q",
        "technology",
        path,
        {"size": 1024, "distance": "Cosine", "hnsw_m": 16, "hnsw_ef_construct": 100},
    )
    assert calls[0] == (
        "create",
        {"size": 1024, "distance": "Cosine", "hnsw_m": 16, "hnsw_ef_construct": 100},
    )
    assert calls[1] == ("upsert", ["p1", "p2"])


def test_make_baseline_importer_wires_ops_in_order(tmp_path, monkeypatch):
    calls = []

    class _RC:
        def download_asset(self, tag, asset, dest, sha):
            calls.append(("download", asset, sha))
            return str(dest)

    monkeypatch.setattr(
        restore_ops,
        "decompress",
        lambda p: calls.append(("decompress", p)) or f"{p}.out",
    )
    monkeypatch.setattr(
        restore_ops, "restore_qdrant_snapshot", lambda *a: calls.append(("qdrant", a))
    )
    monkeypatch.setattr(restore_ops, "restore_arango_dump", lambda *a: calls.append(("arango", a)))
    monkeypatch.setattr(restore_ops, "ensure_named_graph", lambda *a: calls.append(("graph", a)))
    monkeypatch.setattr(
        restore_ops.lexical_index,
        "ensure_chunk_text_index",
        lambda *a, **k: calls.append(("lexical", a)) or "ready",
    )

    importer = restore_ops.make_baseline_importer(
        _RC(),
        tmp_path,
        "http://q",
        "technology",
        "http://a",
        "technology_kg",
        "root",
        "pw",
    )
    entry = {
        "tag": "baseline-2026-06",
        "head_sha": "abc123",
        "assets": {
            "qdrant": "technology.snapshot.zst",
            "arango": "arango-dump.tar.zst",
        },
        "sha256": {"qdrant": "qs", "arango": "as"},
    }
    result = importer(entry)

    assert result == {"head_sha": "abc123", "chunk_text_status": "ready"}
    kinds = [c[0] for c in calls]
    assert kinds.count("download") == 2
    # entry carries no "format" -> snapshot leg: make_baseline_importer's restore_q lambda
    # decompresses the Qdrant asset itself (its second decompress, alongside the arango
    # leg's), and restore_qdrant_snapshot receives the DECOMPRESSED path -- unchanged
    # transition-window behavior, now living in the lambda instead of import_baseline.
    assert kinds.count("decompress") == 2
    decompress_paths = [c[1] for c in calls if c[0] == "decompress"]
    assert any(str(p).endswith("technology.snapshot.zst") for p in decompress_paths)
    assert "qdrant" in kinds and "arango" in kinds
    qdrant_call = next(c for c in calls if c[0] == "qdrant")
    assert qdrant_call[1][2].endswith("technology.snapshot.zst.out")
    # named graph created, THEN the lexical index warmed, against the same qdrant url/collection
    assert kinds[-2:] == ["graph", "lexical"]
    assert calls[-1] == ("lexical", ("http://q", "technology"))


def test_make_baseline_importer_routes_bundle_format_without_decompressing_qdrant(
    tmp_path, monkeypatch
):
    """A bundle-format manifest entry must skip the snapshot leg entirely: no decompress
    of the Qdrant asset, and restore_qdrant_bundle (not restore_qdrant_snapshot) runs,
    receiving the still-compressed .zst plus the manifest's qdrant_collection config."""
    calls = []

    class _RC:
        def download_asset(self, tag, asset, dest, sha):
            calls.append(("download", asset, sha))
            return str(dest)

    monkeypatch.setattr(
        restore_ops,
        "decompress",
        lambda p: calls.append(("decompress", p)) or f"{p}.out",
    )
    monkeypatch.setattr(
        restore_ops, "restore_qdrant_bundle", lambda *a: calls.append(("bundle", a))
    )
    monkeypatch.setattr(
        restore_ops, "restore_qdrant_snapshot", lambda *a: calls.append(("snapshot", a))
    )
    monkeypatch.setattr(restore_ops, "restore_arango_dump", lambda *a: calls.append(("arango", a)))
    monkeypatch.setattr(restore_ops, "ensure_named_graph", lambda *a: calls.append(("graph", a)))
    monkeypatch.setattr(
        restore_ops.lexical_index,
        "ensure_chunk_text_index",
        lambda *a, **k: calls.append(("lexical", a)) or "ready",
    )

    importer = restore_ops.make_baseline_importer(
        _RC(), tmp_path, "http://q", "technology", "http://a", "technology_kg", "root", "pw"
    )
    cfg = {"size": 1024, "distance": "Cosine", "hnsw_m": 16, "hnsw_ef_construct": 100}
    entry = {
        "tag": "baseline-2026-06",
        "head_sha": "abc123",
        "format": "bundle",
        "qdrant_collection": cfg,
        "assets": {"qdrant": "technology.jsonl.zst", "arango": "arango-dump.tar.zst"},
        "sha256": {"qdrant": "qs", "arango": "as"},
    }
    result = importer(entry)

    assert result == {"head_sha": "abc123", "chunk_text_status": "ready"}
    kinds = [c[0] for c in calls]
    assert "snapshot" not in kinds  # bundle format never touches the snapshot leg
    # only the arango asset is decompressed; the bundle streams its .zst directly
    assert kinds.count("decompress") == 1
    bundle_call = next(c for c in calls if c[0] == "bundle")
    assert bundle_call[1][0] == "http://q"
    assert bundle_call[1][1] == "technology"
    assert bundle_call[1][2].endswith("technology.jsonl.zst")
    assert bundle_call[1][3] == cfg


def test_make_baseline_importer_prints_the_chunk_text_status(tmp_path, monkeypatch, capsys):
    """An ordinary `update` run through a real baseline restore leaves a visible trace,
    even though the status isn't threaded into updater.update's structured receipt."""

    class _RC:
        def download_asset(self, tag, asset, dest, sha):
            return str(dest)

    monkeypatch.setattr(restore_ops, "decompress", lambda p: f"{p}.out")
    monkeypatch.setattr(restore_ops, "restore_qdrant_snapshot", lambda *a: None)
    monkeypatch.setattr(restore_ops, "restore_arango_dump", lambda *a: None)
    monkeypatch.setattr(restore_ops, "ensure_named_graph", lambda *a: None)
    monkeypatch.setattr(
        restore_ops.lexical_index, "ensure_chunk_text_index", lambda *a, **k: "building"
    )

    importer = restore_ops.make_baseline_importer(
        _RC(), tmp_path, "http://q", "technology", "http://a", "technology_kg", "root", "pw"
    )
    entry = {
        "tag": "baseline-2026-06",
        "head_sha": "abc123",
        "assets": {"qdrant": "technology.snapshot.zst", "arango": "arango-dump.tar.zst"},
        "sha256": {"qdrant": "qs", "arango": "as"},
    }

    importer(entry)

    assert "chunk_text index: building" in capsys.readouterr().out
