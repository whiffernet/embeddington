from pathlib import Path

import pytest

from consumer import cursor_store, release_client, updater, writers
from embeddington.format import bundle as bundle_mod
from embeddington.format import records
from embeddington.format.manifest import sha256_file


class _FakeFetcher:
    def __init__(self, urls):
        self._urls = urls

    def get(self, url):
        return self._urls[url]

    def download(self, url, dest):
        data = self._urls[url]
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest


def _diff_bundle_bytes(tmp_path, prev, head):
    recs = [
        records.header("1.0", prev, head, points=1, entities=0, edges=0),
        records.point_upsert(f"pt-{head}", [0.1], {"filename": f"{head}.md"}),
    ]
    p = tmp_path / f"diff-{head}.jsonl.zst"
    bundle_mod.write_bundle(p, recs)
    return p.read_bytes(), sha256_file(p)


def _setup(tmp_path):
    """Build a manifest + two diffs served by a fake fetcher; return (rc, manifest)."""
    repo = "me/embeddington"

    def url(tag, name):
        return f"https://github.com/{repo}/releases/download/{tag}/{name}"

    b1, s1 = _diff_bundle_bytes(tmp_path, "c3d4", "e5f6")
    b2, s2 = _diff_bundle_bytes(tmp_path, "e5f6", "a7b8")
    manifest = {
        "schema_version": "1.0",
        "baselines": [
            {
                "tag": "baseline-2026-06",
                "head_sha": "c3d4",
                "points": 0,
                "entities": 0,
                "edges": 0,
                "assets": {"qdrant": "q.zst", "arango": "a.zst"},
                "sha256": {"qdrant": "x", "arango": "y"},
            }
        ],
        "diffs": [
            {
                "prev_sha": "c3d4",
                "head_sha": "e5f6",
                "asset": "diff-e5f6.jsonl.zst",
                "sha256": s1,
            },
            {
                "prev_sha": "e5f6",
                "head_sha": "a7b8",
                "asset": "diff-a7b8.jsonl.zst",
                "sha256": s2,
            },
        ],
    }
    import json

    fetcher = _FakeFetcher(
        {
            url("diffs", "manifest.json"): json.dumps(manifest).encode(),
            url("diffs", "diff-e5f6.jsonl.zst"): b1,
            url("diffs", "diff-a7b8.jsonl.zst"): b2,
        }
    )
    return release_client.ReleaseClient(fetcher, repo=repo), manifest


def test_fresh_install_calls_baseline_then_applies_diffs(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    rc, _ = _setup(tmp_path)
    qw = writers.QdrantConsumerWriter(fake_qdrant_client, "technology")
    aw = writers.ArangoConsumerWriter(fake_arango_db)
    imported = []
    cursor_path = tmp_path / "data" / ".cursor"

    result = updater.update(
        rc,
        qw,
        aw,
        cursor_path,
        work_dir=tmp_path / "work",
        baseline_importer=lambda b: imported.append(b["tag"]),
    )

    assert imported == ["baseline-2026-06"]  # baseline restored first
    assert result["applied"] == 2  # then both diffs
    assert result["baseline"]["tag"] == "baseline-2026-06"  # entry surfaced for messaging
    assert cursor_store.read_cursor(cursor_path) == "a7b8"
    assert set(fake_qdrant_client.points) == {"pt-e5f6", "pt-a7b8"}


def test_up_to_date_is_noop(tmp_path, fake_qdrant_client, fake_arango_db):
    rc, _ = _setup(tmp_path)
    qw = writers.QdrantConsumerWriter(fake_qdrant_client, "technology")
    aw = writers.ArangoConsumerWriter(fake_arango_db)
    cursor_path = tmp_path / ".cursor"
    cursor_store.write_cursor(cursor_path, "a7b8")  # already at head

    result = updater.update(
        rc,
        qw,
        aw,
        cursor_path,
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: None,
    )
    assert result["mode"] == "up_to_date" and result["applied"] == 0
    assert fake_qdrant_client.points == {}


def test_mid_chain_applies_only_remaining(tmp_path, fake_qdrant_client, fake_arango_db):
    rc, _ = _setup(tmp_path)
    qw = writers.QdrantConsumerWriter(fake_qdrant_client, "technology")
    aw = writers.ArangoConsumerWriter(fake_arango_db)
    cursor_path = tmp_path / ".cursor"
    cursor_store.write_cursor(cursor_path, "e5f6")  # one diff behind

    result = updater.update(
        rc,
        qw,
        aw,
        cursor_path,
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: None,
    )
    assert result["mode"] == "diffs" and result["applied"] == 1
    assert set(fake_qdrant_client.points) == {"pt-a7b8"}
    assert cursor_store.read_cursor(cursor_path) == "a7b8"


def test_baseline_required_without_importer_raises(tmp_path, fake_qdrant_client, fake_arango_db):
    rc, _ = _setup(tmp_path)
    qw = writers.QdrantConsumerWriter(fake_qdrant_client, "technology")
    aw = writers.ArangoConsumerWriter(fake_arango_db)
    with pytest.raises(updater.BaselineRequired):
        updater.update(
            rc,
            qw,
            aw,
            tmp_path / ".cursor",
            work_dir=tmp_path / "w",
            baseline_importer=None,
        )  # fresh install, no importer
