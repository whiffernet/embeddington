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
    assert kinds.count("decompress") == 2
    assert "qdrant" in kinds and "arango" in kinds
    # named graph created, THEN the lexical index warmed, against the same qdrant url/collection
    assert kinds[-2:] == ["graph", "lexical"]
    assert calls[-1] == ("lexical", ("http://q", "technology"))


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
