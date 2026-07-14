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


def _legacy(tmp_path, name, sha):
    """Write a legacy cursor file and return its path."""
    p = tmp_path / name / "data" / ".cursor"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(sha, encoding="utf-8")
    return p


def _writers(fake_qdrant_client, fake_arango_db):
    return (
        writers.QdrantConsumerWriter(fake_qdrant_client, "technology"),
        writers.ArangoConsumerWriter(fake_arango_db),
    )


def _populate(fake_qdrant_client, fake_arango_db):
    """Make BOTH stores look like a healthy, fully-restored install."""
    fake_qdrant_client.points = {"pre-existing": {"vector": [0.1], "payload": {}}}
    fake_arango_db.collections["entities_v2"]["e1"] = {"_key": "e1"}


def test_adopts_legacy_cursor_instead_of_re_baselining(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    _populate(fake_qdrant_client, fake_arango_db)
    legacy = _legacy(tmp_path, "clone", "e5f6")  # one diff behind head
    imported = []
    cursor_path = tmp_path / "state" / ".cursor"  # new location: empty

    result = updater.update(
        rc,
        qw,
        aw,
        cursor_path,
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: imported.append(b["tag"]),
        legacy_cursors=[legacy],
    )

    assert imported == []  # the whole point: no 828 MB re-download
    assert result["mode"] == "diffs" and result["applied"] == 1
    assert result["adopted_from"] == legacy
    assert cursor_store.read_cursor(cursor_path) == "a7b8"  # migrated forward


def test_adopts_the_candidate_furthest_along_the_chain(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    """Ranking is by chain position, NOT by list order."""
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    stale = _legacy(tmp_path, "home", "c3d4")  # two diffs behind (the cron-bug cursor)
    fresh = _legacy(tmp_path, "clone", "e5f6")  # one diff behind

    # fresh is LAST here and FIRST in the reversed case; a "first wins" or "last wins"
    # implementation passes one and fails the other. Only true ranking passes both.
    for order in ([stale, fresh], [fresh, stale]):
        result = updater.update(
            rc,
            qw,
            aw,
            tmp_path / f"state-{len(order)}-{order[0].parent.parent.name}" / ".cursor",
            work_dir=tmp_path / "w",
            baseline_importer=lambda b: None,
            legacy_cursors=order,
        )
        assert result["adopted_from"] == fresh  # nearest HEAD wins
        assert result["applied"] == 1  # only the remaining diff


def test_off_chain_legacy_cursor_is_still_adopted_so_compaction_re_baselines(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    """A pre-compaction cursor must re-baseline normally, NOT hit the guard.

    Before this fix, an off-chain candidate was discarded, cursor stayed None, and the
    populated-store guard refused the restore -- telling the user to re-run from their
    clone, which is exactly what they had just done.
    """
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    _populate(fake_qdrant_client, fake_arango_db)
    old = _legacy(tmp_path, "clone", "0ldc0mpacted")  # no longer on the retained chain
    imported = []

    result = updater.update(
        rc,
        qw,
        aw,
        tmp_path / "state" / ".cursor",
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: imported.append(b["tag"]),
        legacy_cursors=[old],
    )

    assert imported == ["baseline-2026-06"]  # legitimate re-baseline, not refused
    assert result["mode"] == "baseline"
    assert result["adopted_from"] == old


def test_candidate_whose_plan_raises_is_not_adopted_over_a_usable_fallback(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    """A candidate whose plan_update RAISES must never be adopted, even as a fallback.

    Before this fix, ``_adopt_legacy_cursor`` captured the fallback tuple BEFORE calling
    plan_update. A candidate whose sha started a broken diff-chain sub-sequence (ChainGapError)
    got captured into ``fallback`` first, and the ``continue`` in the except-block was a no-op
    -- that already-captured, unusable sha won even though a later, genuinely off-chain (and
    therefore adoptable) candidate followed it. This manifest is built so plan_update actually
    raises for ``bad`` -- it is not mocked.
    """
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
            # A broken sub-chain reachable ONLY from "badsha": the walk from "badsha" hits
            # this pair first and raises ChainGapError before ever reaching the real chain
            # below. Placed ahead of the real chain so it does not affect walks that start
            # at "c3d4" (chain_from finds the FIRST matching prev_sha and walks from there).
            {"prev_sha": "badsha", "head_sha": "middle", "asset": "junk1.zst", "sha256": "dead1"},
            {"prev_sha": "WRONG", "head_sha": "final", "asset": "junk2.zst", "sha256": "dead2"},
            {"prev_sha": "c3d4", "head_sha": "e5f6", "asset": "diff-e5f6.jsonl.zst", "sha256": s1},
            {"prev_sha": "e5f6", "head_sha": "a7b8", "asset": "diff-a7b8.jsonl.zst", "sha256": s2},
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
    rc = release_client.ReleaseClient(fetcher, repo=repo)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)

    bad = _legacy(tmp_path, "bad", "badsha")  # plan_update raises ChainGapError for this sha
    good = _legacy(tmp_path, "good", "0ldc0mpacted")  # genuinely off-chain -> usable fallback
    imported = []

    result = updater.update(
        rc,
        qw,
        aw,
        tmp_path / "state" / ".cursor",
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: imported.append(b["tag"]),
        legacy_cursors=[bad, good],
    )

    assert result["adopted_from"] == good  # NOT bad, even though bad was seen first
    assert imported == ["baseline-2026-06"]


def test_guard_refuses_baseline_into_a_populated_store(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    _populate(fake_qdrant_client, fake_arango_db)
    imported = []

    with pytest.raises(updater.BaselineRefused) as exc:
        updater.update(
            rc,
            qw,
            aw,
            tmp_path / "state" / ".cursor",  # no cursor, nothing to adopt
            work_dir=tmp_path / "w",
            baseline_importer=lambda b: imported.append(b["tag"]),
        )

    assert imported == []  # refused BEFORE the download
    assert "technology" in str(exc.value) and "--force-baseline" in str(exc.value)


def test_blank_cursor_file_is_treated_as_absent(tmp_path, fake_qdrant_client, fake_arango_db):
    """A torn write leaves a 0-byte (or whitespace-only) cursor file.

    read_cursor() normalizes that blank content to None (its ``.strip() or None`` contract),
    so it reads back as "no cursor" -- and the populated-store guard must still fire on it,
    exactly as it does when the file is absent outright.
    """
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    _populate(fake_qdrant_client, fake_arango_db)
    cursor_path = tmp_path / "state" / ".cursor"
    cursor_path.parent.mkdir(parents=True)
    cursor_path.write_text("\n", encoding="utf-8")
    imported = []

    with pytest.raises(updater.BaselineRefused):
        updater.update(
            rc,
            qw,
            aw,
            cursor_path,
            work_dir=tmp_path / "w",
            baseline_importer=lambda b: imported.append(b["tag"]),
        )

    assert imported == []


def test_guard_allows_baseline_on_a_genuinely_empty_store(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    imported = []

    result = updater.update(
        rc,
        qw,
        aw,
        tmp_path / "state" / ".cursor",
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: imported.append(b["tag"]),
    )

    assert imported == ["baseline-2026-06"]  # fresh install still works
    assert result["mode"] == "baseline"


def test_guard_allows_retry_of_an_interrupted_baseline_import(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    """Qdrant restores before Arango and the cursor is written last.

    So an interrupted import leaves Qdrant populated, Arango empty, no cursor. That state
    is re-runnable today and must stay that way -- refusing it would strand the user.
    """
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    fake_qdrant_client.points = {"half": {"vector": [0.1], "payload": {}}}  # Arango still empty
    imported = []

    result = updater.update(
        rc,
        qw,
        aw,
        tmp_path / "state" / ".cursor",
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: imported.append(b["tag"]),
    )

    assert imported == ["baseline-2026-06"]  # the retry completes
    assert result["mode"] == "baseline"


def test_force_baseline_restores_even_when_the_cursor_says_up_to_date(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    """The flag's real use case: a corrupt store with a perfectly good cursor.

    Without the short-circuit this planned "up_to_date", printed "already the latest",
    exited 0, and restored nothing -- the flag was decorative.
    """
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    _populate(fake_qdrant_client, fake_arango_db)
    cursor_path = tmp_path / "state" / ".cursor"
    cursor_store.write_cursor(cursor_path, "a7b8")  # at HEAD: nothing to do, normally
    imported = []

    result = updater.update(
        rc,
        qw,
        aw,
        cursor_path,
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: imported.append(b["tag"]),
        force_baseline=True,
    )

    assert imported == ["baseline-2026-06"]  # it actually restores
    assert result["mode"] == "baseline"
