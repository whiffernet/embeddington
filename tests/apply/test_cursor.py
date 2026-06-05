import pytest

from embeddington import errors
from embeddington.apply import cursor


def _manifest(schema_version="1.0"):
    return {
        "schema_version": schema_version,
        "baselines": [
            {
                "tag": "baseline-2026-06",
                "head_sha": "c3d4",
                "points": 10,
                "entities": 1,
                "edges": 1,
                "assets": {"qdrant": "q.zst", "arango": "a.zst"},
                "sha256": {"qdrant": "x", "arango": "y"},
            }
        ],
        "diffs": [
            {
                "prev_sha": "c3d4",
                "head_sha": "e5f6",
                "asset": "diff-e5f6.jsonl.zst",
                "sha256": "1",
            },
            {
                "prev_sha": "e5f6",
                "head_sha": "a7b8",
                "asset": "diff-a7b8.jsonl.zst",
                "sha256": "2",
            },
        ],
    }


def test_fresh_user_gets_baseline_plus_all_diffs():
    plan = cursor.plan_update(None, _manifest())
    assert plan.mode == "baseline"
    assert plan.baseline["tag"] == "baseline-2026-06"
    assert [d["head_sha"] for d in plan.diffs] == ["e5f6", "a7b8"]


def test_cursor_at_head_is_up_to_date():
    plan = cursor.plan_update("a7b8", _manifest())
    assert plan.mode == "up_to_date"
    assert plan.diffs == []


def test_cursor_in_chain_applies_only_remaining_diffs():
    plan = cursor.plan_update("e5f6", _manifest())
    assert plan.mode == "diffs"
    assert [d["head_sha"] for d in plan.diffs] == ["a7b8"]


def test_cursor_at_baseline_head_applies_all_diffs():
    plan = cursor.plan_update("c3d4", _manifest())
    assert plan.mode == "diffs"
    assert [d["head_sha"] for d in plan.diffs] == ["e5f6", "a7b8"]


def test_unreachable_cursor_falls_back_to_baseline():
    plan = cursor.plan_update("deadbeef", _manifest())  # cursor not in retained chain
    assert plan.mode == "baseline"
    assert [d["head_sha"] for d in plan.diffs] == ["e5f6", "a7b8"]


def test_schema_major_bump_is_gated():
    with pytest.raises(errors.SchemaVersionError):
        cursor.plan_update("e5f6", _manifest(schema_version="2.0"))


def test_chain_gap_raises():
    m = _manifest()
    m["diffs"][1]["prev_sha"] = "WRONG"  # break the chain
    with pytest.raises(errors.ChainGapError):
        cursor.plan_update("c3d4", m)


def test_multiple_baselines_picks_latest():
    """baselines[-1] (latest) is chosen; old baseline's SHA is not used."""
    m = {
        "schema_version": "1.0",
        "baselines": [
            {
                "tag": "baseline-old",
                "head_sha": "old1",
                "points": 5,
                "entities": 1,
                "edges": 1,
                "assets": {"qdrant": "q.zst", "arango": "a.zst"},
                "sha256": {"qdrant": "x", "arango": "y"},
            },
            {
                "tag": "baseline-new",
                "head_sha": "new1",
                "points": 10,
                "entities": 2,
                "edges": 2,
                "assets": {"qdrant": "q2.zst", "arango": "a2.zst"},
                "sha256": {"qdrant": "p", "arango": "q"},
            },
        ],
        "diffs": [
            {
                "prev_sha": "new1",
                "head_sha": "new2",
                "asset": "d.jsonl.zst",
                "sha256": "1",
            },
        ],
    }
    plan = cursor.plan_update(None, m)
    assert plan.mode == "baseline"
    assert plan.baseline["tag"] == "baseline-new"
    assert [d["head_sha"] for d in plan.diffs] == ["new2"]

    fallback = cursor.plan_update(
        "old1", m
    )  # old baseline SHA is unreachable -> latest baseline
    assert fallback.mode == "baseline"
    assert fallback.baseline["tag"] == "baseline-new"


def test_fresh_install_no_diffs_returns_baseline_with_empty_diffs():
    """A manifest with no diffs yet: fresh install gets baseline + empty diff list."""
    m = {
        "schema_version": "1.0",
        "baselines": [
            {
                "tag": "baseline-only",
                "head_sha": "b1",
                "points": 100,
                "entities": 10,
                "edges": 5,
                "assets": {"qdrant": "q.zst", "arango": "a.zst"},
                "sha256": {"qdrant": "x", "arango": "y"},
            },
        ],
        "diffs": [],
    }
    plan = cursor.plan_update(None, m)
    assert plan.mode == "baseline"
    assert plan.baseline["tag"] == "baseline-only"
    assert plan.diffs == []


def test_already_at_baseline_head_with_no_diffs_is_up_to_date():
    """After installing the only baseline, with no diffs, client is up_to_date."""
    m = {
        "schema_version": "1.0",
        "baselines": [
            {
                "tag": "baseline-only",
                "head_sha": "b1",
                "points": 100,
                "entities": 10,
                "edges": 5,
                "assets": {"qdrant": "q.zst", "arango": "a.zst"},
                "sha256": {"qdrant": "x", "arango": "y"},
            },
        ],
        "diffs": [],
    }
    plan = cursor.plan_update("b1", m)
    assert plan.mode == "up_to_date"
    assert plan.diffs == []
