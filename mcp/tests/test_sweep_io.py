"""Unit tests for the sweep's pure serialization layer."""

import json

import sweep_io


def _combo():
    return {
        "edge_budget": 40,
        "top_k": 5,
        "dedup": "on",
        "q": {
            "case2_minimal": {
                "tokens": 100,
                "ms_all": [10.0, 12.0, 11.0, 13.0, 11.5],
                "returned": 7,
                "trunc": False,
                "ret": 0.2,
                "pp": 1.0,
                "err": {},
                "kept_ids": ["e1", "e2"],
                "calls": {"embed": 1, "qdrant_search": 1, "arango_stratified": 2},
            }
        },
    }


def test_latency_summary_median_and_iqr():
    s = sweep_io.latency_summary([10.0, 12.0, 11.0, 13.0, 11.5])
    assert s["ms_median"] == 11.5
    assert s["ms_iqr"] > 0


def test_serialize_run_is_json_and_carries_binding():
    doc = sweep_io.serialize_run(
        rows=[_combo()],
        ground_truth={
            "case2_minimal": {"gt_ids": {"e9"}, "k_eff": 1, "pool_size": 3, "pool_preds": {"P"}}
        },
        binding={"baseline": "baseline-2026-07b", "points": 1, "entities": 1, "edges": 1},
        meta={"git_sha": "abc", "reps": 5, "tag": "t"},
    )
    parsed = json.loads(json.dumps(doc))  # must be JSON-serializable (sets -> lists)
    assert parsed["binding"]["baseline"] == "baseline-2026-07b"
    q = parsed["rows"][0]["q"]["case2_minimal"]
    assert q["kept_ids"] == ["e1", "e2"]
    assert q["ms_median"] == 11.5
    assert parsed["ground_truth"]["case2_minimal"]["gt_ids"] == ["e9"]


def test_serialize_run_tolerates_already_normalized_entries():
    """entries with ms_median already present and no ms_all pass through unchanged."""
    combo = {
        "edge_budget": 40,
        "top_k": 5,
        "dedup": "on",
        "q": {
            "case2_minimal": {
                "tokens": 100,
                "ms_median": 11.5,
                "ms_iqr": 2.0,
                "returned": 7,
                "trunc": False,
                "ret": 0.2,
                "pp": 1.0,
                "err": {},
                "kept_ids": ["e1", "e2"],
                "calls": {"embed": 1, "qdrant_search": 1, "arango_stratified": 2},
            }
        },
    }
    doc = sweep_io.serialize_run(
        rows=[combo],
        ground_truth={},
        binding={"baseline": "baseline-2026-07b", "points": 1, "entities": 1, "edges": 1},
        meta={"git_sha": "abc", "reps": 5, "tag": "t"},
    )
    q = doc["rows"][0]["q"]["case2_minimal"]
    assert q["ms_median"] == 11.5
    assert q["ms_iqr"] == 2.0
    assert "ms_all" not in q
