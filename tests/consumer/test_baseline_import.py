"""Tests for the consumer baseline-import orchestration (Plan 3b / Phase D)."""

from consumer import baseline_import


def _entry():
    return {
        "tag": "baseline-2026-06",
        "head_sha": "c3d4",
        "points": 62717,
        "entities": 242937,
        "edges": 499836,
        "assets": {
            "qdrant": "technology.snapshot.zst",
            "arango": "arango-dump.tar.zst",
        },
        "sha256": {"qdrant": "qsha", "arango": "asha"},
    }


def test_import_baseline_orchestrates_in_order(tmp_path):
    calls = []

    def download_asset(tag, asset, dest, sha256):
        calls.append(("download", tag, asset, sha256))
        return str(dest)

    def decompress(path):
        calls.append(("decompress", path))
        return path[:-4]  # strip ".zst"

    def restore_qdrant(snap):
        calls.append(("restore_qdrant", snap))

    def restore_arango(dump):
        calls.append(("restore_arango", dump))

    def ensure_graph():
        calls.append(("ensure_graph",))

    def ensure_lexical_index():
        calls.append(("ensure_lexical_index",))
        return "ready"

    result = baseline_import.import_baseline(
        _entry(),
        tmp_path,
        download_asset,
        decompress,
        restore_qdrant,
        restore_arango,
        ensure_graph,
        ensure_lexical_index,
    )

    assert result["head_sha"] == "c3d4"  # still seeds the cursor
    assert result["chunk_text_status"] == "ready"  # the ensure's status lands in the result
    kinds = [c[0] for c in calls]
    # both assets downloaded (checksum-verified), both decompressed, both restored, graph created
    assert kinds.count("download") == 2
    assert kinds.count("decompress") == 2
    assert "restore_qdrant" in kinds and "restore_arango" in kinds
    # the named graph is created LAST, then the lexical index is warmed AFTER it
    assert kinds[-2:] == ["ensure_graph", "ensure_lexical_index"]
    # checksums were passed through to the downloader
    assert ("download", "baseline-2026-06", "technology.snapshot.zst", "qsha") in calls


def test_import_baseline_surfaces_a_degraded_lexical_status(tmp_path):
    """A degraded ensure (e.g. Qdrant not green yet) must not fail the import --
    the status is surfaced for the caller to report, not raised."""

    def noop(*a, **k):
        return None

    result = baseline_import.import_baseline(
        _entry(),
        tmp_path,
        download_asset=lambda tag, asset, dest, sha256: str(dest),
        decompress=lambda p: p[:-4],
        restore_qdrant=noop,
        restore_arango=noop,
        ensure_graph=noop,
        ensure_lexical_index=lambda: "building",
    )

    assert result["chunk_text_status"] == "building"
