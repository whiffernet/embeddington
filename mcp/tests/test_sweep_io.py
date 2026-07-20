"""Unit tests for the sweep's pure serialization layer."""

import json

import pytest
import sweep_io
from battery_queries import IDENTIFIER_QUERIES
from battery_queries import QUERIES as FIXED_QUERIES


def test_select_cohort_fixed_default():
    assert sweep_io.select_cohort("fixed") is FIXED_QUERIES


def test_select_cohort_identifier():
    assert sweep_io.select_cohort("identifier") is IDENTIFIER_QUERIES


def test_select_cohort_unknown_raises():
    with pytest.raises(ValueError, match="unknown cohort"):
        sweep_io.select_cohort("bogus")


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


def test_render_title_contains_tag():
    title = sweep_io.render_title("2026-07-20-pr6-final")
    assert "2026-07-20-pr6-final" in title
    assert "2026-07-17" not in title


def test_render_title_is_h1():
    assert sweep_io.render_title("2026-07-20-pr6-final").startswith("# ")


def test_render_knee_verdict_differs_suggests_no_action_claim():
    verdict = sweep_io.render_knee_verdict(20, 5, (40, 5))
    assert "suggests" in verdict
    assert "defaults unchanged by this run" in verdict
    assert "default updated" not in verdict


def test_render_knee_verdict_matches_shipped_no_change():
    verdict = sweep_io.render_knee_verdict(40, 5, (40, 5))
    assert "no change" in verdict


class _FakeEmbedClient:
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


@pytest.mark.asyncio
async def test_wrap_counting_embed_batch_increments_call_counts():
    sweep_io.CALL_COUNTS.clear()
    fake = _FakeEmbedClient()
    sweep_io.wrap_counting(fake, "embed_batch", "embed_batch")

    await fake.embed_batch(["a", "b"])

    assert sweep_io.CALL_COUNTS["embed_batch"] == 1


def test_render_finding_2_reports_measured_peak_not_hardcoded_40():
    """#44 final-review B2: a curve peaking at edge_budget=80 (like the real
    PR 6 sweep) must not be described as peaking at 40, and none of the old
    hardcoded PR1/#28-era constants may survive into the rendered text.
    """
    curve = [
        (20, {"mean_ret": 0.373, "mean_returned": 19.2, "mean_pp": 0.823}),
        (40, {"mean_ret": 0.745, "mean_returned": 25.2, "mean_pp": 0.977}),
        (60, {"mean_ret": 0.900, "mean_returned": 25.1, "mean_pp": 0.985}),
        (80, {"mean_ret": 0.918, "mean_returned": 24.9, "mean_pp": 0.985}),
        (120, {"mean_ret": 0.882, "mean_returned": 24.3, "mean_pp": 0.985}),
    ]
    text = sweep_io.render_finding_2(curve, top_k=5, edge_budgets=[20, 40, 60, 80, 120])

    assert "edge_budget=80" in text  # the actual peak in this (real) curve
    assert "Retention still peaks at edge_budget=40" not in text
    assert "~28 mean" not in text
    assert "~8.6 mean edges" not in text
    assert "predicate recall stays" not in text
    assert "orphan-node trim fix" not in text
    assert "delivery *inverted*" not in text


def test_render_finding_2_reflects_never_rising_curve():
    """The identifier cohort's real curve never plateaus within the grid
    (strictly increasing 20->120) -- the peak must be reported at the top of
    the grid, not misattributed to an interior point.
    """
    curve = [
        (20, {"mean_ret": 0.050, "mean_returned": 10.0, "mean_pp": 0.500}),
        (40, {"mean_ret": 0.150, "mean_returned": 11.2, "mean_pp": 0.500}),
        (60, {"mean_ret": 0.225, "mean_returned": 11.2, "mean_pp": 0.500}),
        (80, {"mean_ret": 0.350, "mean_returned": 11.2, "mean_pp": 0.500}),
        (120, {"mean_ret": 0.425, "mean_returned": 11.5, "mean_pp": 0.500}),
    ]
    text = sweep_io.render_finding_2(curve, top_k=5, edge_budgets=[20, 40, 60, 80, 120])
    assert "edge_budget=120" in text


def test_render_finding_2_range_reflects_min_and_max():
    curve = [
        (20, {"mean_ret": 0.1, "mean_returned": 5.0, "mean_pp": 0.400}),
        (120, {"mean_ret": 0.9, "mean_returned": 30.0, "mean_pp": 0.950}),
    ]
    text = sweep_io.render_finding_2(curve, top_k=5, edge_budgets=[20, 120])
    assert "5.0" in text and "30.0" in text
    assert "0.400" in text and "0.950" in text


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
