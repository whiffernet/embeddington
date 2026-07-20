import os
import shutil
from pathlib import Path

import pytest
from arango.exceptions import ArangoServerError

from consumer import cursor_store, release_client, state_paths, updater, writers
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


def test_ensure_index_called_after_diffs(tmp_path, fake_qdrant_client, fake_arango_db):
    rc, _ = _setup(tmp_path)
    qw = writers.QdrantConsumerWriter(fake_qdrant_client, "technology")
    aw = writers.ArangoConsumerWriter(fake_arango_db)
    cursor_path = tmp_path / ".cursor"
    cursor_store.write_cursor(cursor_path, "e5f6")  # one diff behind
    calls = []

    result = updater.update(
        rc,
        qw,
        aw,
        cursor_path,
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: None,
        ensure_index=lambda: calls.append("ran"),
    )
    assert result["mode"] == "diffs"
    assert calls == ["ran"]


def test_ensure_index_not_called_when_up_to_date(tmp_path, fake_qdrant_client, fake_arango_db):
    rc, _ = _setup(tmp_path)
    qw = writers.QdrantConsumerWriter(fake_qdrant_client, "technology")
    aw = writers.ArangoConsumerWriter(fake_arango_db)
    cursor_path = tmp_path / ".cursor"
    cursor_store.write_cursor(cursor_path, "a7b8")  # already at head
    calls = []

    result = updater.update(
        rc,
        qw,
        aw,
        cursor_path,
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: None,
        ensure_index=lambda: calls.append("ran"),
    )
    assert result["mode"] == "up_to_date"
    assert calls == []


def test_ensure_index_default_none_is_backward_compatible(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    rc, _ = _setup(tmp_path)
    qw = writers.QdrantConsumerWriter(fake_qdrant_client, "technology")
    aw = writers.ArangoConsumerWriter(fake_arango_db)
    cursor_path = tmp_path / ".cursor"
    cursor_store.write_cursor(cursor_path, "e5f6")
    # No ensure_index passed at all -> must not raise.
    result = updater.update(
        rc, qw, aw, cursor_path, work_dir=tmp_path / "w", baseline_importer=lambda b: None
    )
    assert result["mode"] == "diffs"


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

    # fresh is LAST in the first pass and FIRST in the second; a "first wins" or "last wins"
    # implementation passes one and fails the other. Only true ranking passes both. The files
    # are re-written each pass because adoption retires (renames) the one it takes.
    for reverse in (False, True):
        stale = _legacy(tmp_path, "home", "c3d4")  # two diffs behind (the cron-bug cursor)
        fresh = _legacy(tmp_path, "clone", "e5f6")  # one diff behind
        order = [fresh, stale] if reverse else [stale, fresh]
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

    So an import interrupted between the two leaves Qdrant populated and Arango not merely
    empty but ABSENT -- technology_kg is created by arangorestore --create-database, which
    never ran. entity_count() must survive that (404) and report 0, or the retry of an
    828 MB import dies in a DocumentCountError traceback instead of restoring.
    """
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    fake_qdrant_client.points = {"half": {"vector": [0.1], "payload": {}}}
    fake_arango_db.db_exists = False  # arangorestore never got to create it
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


def test_sentinel_lets_a_restore_killed_mid_arangorestore_retry(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    """Killed DURING arangorestore: both stores populated, no cursor -- counts can't tell.

    entities_v2 exists and is partially filled, so the populated-store guard would refuse
    the retry and send the user to --force-baseline. The sentinel says "this half-restore is
    OURS", and the retry is allowed through.
    """
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    _populate(fake_qdrant_client, fake_arango_db)  # both stores look full: partial arangorestore
    cursor_path = tmp_path / "state" / ".cursor"
    sentinel = updater.restore_sentinel_path(cursor_path)
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("", encoding="utf-8")
    imported = []

    result = updater.update(
        rc,
        qw,
        aw,
        cursor_path,
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: imported.append(b["tag"]),
    )

    assert imported == ["baseline-2026-06"]  # not refused
    assert result["mode"] == "baseline"
    assert not sentinel.exists()  # and cleared once the cursor is durable


def test_baseline_writes_a_sentinel_while_restoring_and_clears_it_after(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    """The sentinel must exist DURING the import (that is the whole point) and not after."""
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    cursor_path = tmp_path / "state" / ".cursor"
    sentinel = updater.restore_sentinel_path(cursor_path)
    seen = {}

    def importer(baseline):
        seen["during"] = sentinel.exists()
        seen["cursor_during"] = cursor_store.read_cursor(cursor_path)

    updater.update(
        rc,
        qw,
        aw,
        cursor_path,
        work_dir=tmp_path / "w",
        baseline_importer=importer,
    )

    assert seen["during"] is True  # written before the importer runs
    assert seen["cursor_during"] is None  # cursor still last
    assert not sentinel.exists()  # a completed run leaves nothing behind


def test_adopted_cursor_is_persisted_even_when_there_are_no_diffs_to_apply(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    """The unprotected half of the migration: a legacy cursor already AT HEAD.

    Every other adoption path writes the cursor again per applied diff, which masks a missing
    write at adoption time. Here plan_update returns up_to_date and returns early -- if the
    adopted sha is not persisted right where it is adopted, the migration silently never
    happens and the next run starts the whole dance over.
    """
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    _populate(fake_qdrant_client, fake_arango_db)
    legacy = _legacy(tmp_path, "clone", "a7b8")  # already at HEAD: nothing to apply
    cursor_path = tmp_path / "state" / ".cursor"

    result = updater.update(
        rc,
        qw,
        aw,
        cursor_path,
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: None,
        legacy_cursors=[legacy],
    )

    assert result["mode"] == "up_to_date"
    assert cursor_store.read_cursor(cursor_path) == "a7b8"  # the migration is on disk


def test_adoption_retires_the_legacy_cursor_file(tmp_path, fake_qdrant_client, fake_arango_db):
    """The adopted file is renamed, not deleted -- and is never adopted again."""
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    _populate(fake_qdrant_client, fake_arango_db)
    legacy = _legacy(tmp_path, "clone", "a7b8")

    updater.update(
        rc,
        qw,
        aw,
        tmp_path / "state" / ".cursor",
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: None,
        legacy_cursors=[legacy],
    )

    assert not legacy.exists()  # retired...
    retired = legacy.with_name(legacy.name + ".migrated")
    assert retired.read_text(encoding="utf-8").strip() == "a7b8"  # ...but not destroyed


def test_a_lost_state_cursor_after_migration_hits_the_guard_not_a_re_baseline(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    """The whole reason the legacy file must be retired.

    Left in place it goes stale, the chain compacts past it, and a state cursor that later
    goes missing (cron not inheriting EMBEDDINGTON_HOME; a $HOME restore that skipped
    ~/.local/share) resurrects that dead sha as adoption's off-chain fallback. The cursor is
    then truthy, the guard is SKIPPED, and 828 MB lands on top of a healthy store. With the
    file retired, the same situation lands on the guard -- an actionable exit 3.
    """
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    _populate(fake_qdrant_client, fake_arango_db)
    legacy = _legacy(tmp_path, "clone", "a7b8")
    cursor_path = tmp_path / "state" / ".cursor"

    updater.update(  # first run: migrate
        rc,
        qw,
        aw,
        cursor_path,
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: None,
        legacy_cursors=[legacy],
    )
    cursor_path.unlink()  # the state cursor goes missing; the stores are still healthy
    imported = []

    with pytest.raises(updater.BaselineRefused):
        updater.update(
            rc,
            qw,
            aw,
            cursor_path,
            work_dir=tmp_path / "w",
            baseline_importer=lambda b: imported.append(b["tag"]),
            legacy_cursors=state_paths.legacy_cursor_candidates(
                tmp_path / "clone", tmp_path, install_root_dir=tmp_path / "clone"
            ),
        )

    assert imported == []  # no silent 828 MB re-download


def test_an_orphaned_sentinel_does_not_survive_a_run_with_a_good_cursor(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    """The sentinel must never outlive the restore it describes.

    A baseline dies mid-flight and leaves the sentinel (by design). The user then recovers some
    OTHER way -- an old cursor is adopted, or they copy one into place -- so the next run never
    reaches the baseline branch that used to be the sentinel's only unlink. Any run that ends
    up with a known-good cursor must clear it, or it sits there forever with the guard off.
    """
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    _populate(fake_qdrant_client, fake_arango_db)
    cursor_path = tmp_path / "state" / ".cursor"
    cursor_store.write_cursor(cursor_path, "a7b8")  # at HEAD -> up_to_date, an early return
    sentinel = updater.restore_sentinel_path(cursor_path)
    sentinel.write_text("pid=1 started=whenever\n", encoding="utf-8")  # orphan from a dead run

    result = updater.update(
        rc,
        qw,
        aw,
        cursor_path,
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: None,
    )

    assert result["mode"] == "up_to_date"
    assert not sentinel.exists()  # cleared on the known-good-cursor path, not just after import


def test_an_orphaned_sentinel_cannot_disable_the_guard_for_a_later_missing_cursor(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    """The 828 MB hole, end to end.

    Crash mid-restore (sentinel left) -> recover via a legacy cursor -> months later the state
    cursor goes missing (cron not inheriting EMBEDDINGTON_HOME) while the stores are healthy.
    With a stale sentinel around, the guard is skipped and the baseline lands on live data,
    silently, exit 0. The recovery run must have cleared it, so this ends in a refusal.
    """
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    _populate(fake_qdrant_client, fake_arango_db)
    cursor_path = tmp_path / "state" / ".cursor"
    sentinel = updater.restore_sentinel_path(cursor_path)
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("pid=999 started=2026-07-01T00:00:00\n", encoding="utf-8")
    legacy = _legacy(tmp_path, "clone", "a7b8")  # the user's real recovery: an adoptable cursor

    updater.update(  # run 1: recovers WITHOUT taking the baseline branch
        rc,
        qw,
        aw,
        cursor_path,
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: None,
        legacy_cursors=[legacy],
    )
    assert not sentinel.exists()

    cursor_path.unlink()  # run 2: the state cursor vanishes; the stores are still healthy
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

    assert imported == []  # no silent 828 MB over live data


def test_the_sentinel_says_who_wrote_it_and_when(tmp_path, fake_qdrant_client, fake_arango_db):
    """A guard-disabling file with no owner is undebuggable; make it self-describing."""
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    cursor_path = tmp_path / "state" / ".cursor"
    seen = {}

    def importer(baseline):
        seen["content"] = updater.restore_sentinel_path(cursor_path).read_text(encoding="utf-8")

    updater.update(rc, qw, aw, cursor_path, work_dir=tmp_path / "w", baseline_importer=importer)

    assert f"pid={os.getpid()}" in seen["content"]
    assert "started=" in seen["content"]


def test_adoption_retires_every_candidate_it_read_not_just_the_winner(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    """The losers are future adoption candidates too -- and the stalest one wins later.

    Adopt the clone's cursor (at HEAD); $HOME's stale one (the old cron line's, now off-chain)
    loses. Left on disk it stays fully eligible: when the state cursor later goes missing, it
    is adopted as the off-chain FALLBACK, the cursor is truthy, the guard is SKIPPED, and the
    baseline is re-imported over a healthy store. Every file read must be retired.
    """
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    _populate(fake_qdrant_client, fake_arango_db)
    clone = _legacy(tmp_path, "clone", "a7b8")  # at HEAD -> adopted
    home = _legacy(tmp_path, "home", "0ldc0mpacted")  # off-chain -> the loser
    cursor_path = tmp_path / "state" / ".cursor"

    result = updater.update(
        rc,
        qw,
        aw,
        cursor_path,
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: None,
        legacy_cursors=[clone, home],
    )
    assert result["adopted_from"] == clone
    assert not home.exists()  # the LOSER is retired too...
    assert home.with_name(".cursor.migrated").read_text(encoding="utf-8").strip() == "0ldc0mpacted"

    cursor_path.unlink()  # ...so a later missing state cursor lands on the guard
    imported = []
    with pytest.raises(updater.BaselineRefused):
        updater.update(
            rc,
            qw,
            aw,
            cursor_path,
            work_dir=tmp_path / "w",
            baseline_importer=lambda b: imported.append(b["tag"]),
            legacy_cursors=state_paths.legacy_cursor_candidates(
                tmp_path / "clone", tmp_path / "home", install_root_dir=tmp_path / "clone"
            ),
        )
    assert imported == []


def test_a_failed_retirement_warns_instead_of_failing_silently(
    tmp_path, fake_qdrant_client, fake_arango_db, capsys, monkeypatch
):
    """A read-only clone cannot be retired. The update still succeeds -- but say so."""
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    legacy = _legacy(tmp_path, "clone", "a7b8")

    def boom(self, target):
        raise OSError("Read-only file system")

    monkeypatch.setattr(Path, "rename", boom)

    result = updater.update(
        rc,
        qw,
        aw,
        tmp_path / "state" / ".cursor",
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: None,
        legacy_cursors=[legacy],
    )

    assert result["mode"] == "up_to_date"  # not fatal
    err = capsys.readouterr().err
    assert "could not retire" in err and str(legacy) in err


def test_the_refusal_tells_the_user_to_copy_the_cursor_and_that_actually_works(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    """The remedy the message advertises must be a real one.

    It used to say "point --cursor at the old file and it will be migrated forward". It was
    not: --cursor only sets the PATH, so nothing ever reached the state dir and every later
    plain run refused again. The honest remedy is to COPY the file into place -- so the message
    says that, and this test performs literally what it says and checks the next run is clean.
    """
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    _populate(fake_qdrant_client, fake_arango_db)
    cursor_path = tmp_path / "state" / ".cursor"
    old = _legacy(tmp_path, "work", "e5f6")  # a fourth directory the CLI does not probe

    with pytest.raises(updater.BaselineRefused) as exc:
        updater.update(rc, qw, aw, cursor_path, work_dir=tmp_path / "w", baseline_importer=None)

    message = str(exc.value)
    assert "cp " in message and str(cursor_path) in message
    assert "--cursor" not in message  # the old, untrue advice is gone

    cursor_path.parent.mkdir(parents=True, exist_ok=True)  # do exactly what it says
    shutil.copyfile(old, cursor_path)
    imported = []

    result = updater.update(
        rc,
        qw,
        aw,
        cursor_path,
        work_dir=tmp_path / "w",
        baseline_importer=lambda b: imported.append(b["tag"]),
    )

    assert imported == []  # remedied for good: no baseline, now or ever after
    assert result["mode"] == "diffs" and result["applied"] == 1
    assert cursor_store.read_cursor(cursor_path) == "a7b8"


def test_a_broken_arango_propagates_instead_of_looking_like_an_empty_store(
    tmp_path, fake_qdrant_client, fake_arango_db
):
    """The guard's false-negative: a live-but-unreachable Arango must not read as empty.

    Qdrant is full, no cursor, and Arango answers 503 (still replaying its WAL). If entity_count()
    swallowed that as 0, the guard would take it for an interrupted import and re-restore over
    live data. It must blow up instead.
    """
    rc, _ = _setup(tmp_path)
    qw, aw = _writers(fake_qdrant_client, fake_arango_db)
    _populate(fake_qdrant_client, fake_arango_db)
    fake_arango_db.server_error = (0, "service unavailable", 503)
    imported = []

    with pytest.raises(ArangoServerError):
        updater.update(
            rc,
            qw,
            aw,
            tmp_path / "state" / ".cursor",
            work_dir=tmp_path / "w",
            baseline_importer=lambda b: imported.append(b["tag"]),
        )

    assert imported == []  # nothing was downloaded over the live store


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
