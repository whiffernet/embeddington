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
    head = importer(entry)

    assert head == "abc123"
    kinds = [c[0] for c in calls]
    assert kinds.count("download") == 2
    assert kinds.count("decompress") == 2
    assert "qdrant" in kinds and "arango" in kinds
    assert kinds[-1] == "graph"  # named graph created last
