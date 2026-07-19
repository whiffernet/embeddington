"""Unit tests for gold-set metrics (spec §3.4/3.5)."""

import gold_metrics


def test_recall_denominator_is_budget_independent():
    # Same kept set, same relevant set => same recall regardless of any budget.
    kept = {"a", "b"}
    relevant = {"a", "b", "c", "d"}
    assert gold_metrics.gold_recall_at_budget(kept, relevant) == 0.5


def test_recall_none_when_no_relevant():
    assert gold_metrics.gold_recall_at_budget({"a"}, set()) is None


def test_precision_counts_only_kept():
    assert gold_metrics.gold_precision({"a", "b", "x"}, {"a", "b", "c"}) == 2 / 3


def test_precision_none_when_nothing_kept():
    assert gold_metrics.gold_precision(set(), {"a"}) is None


def test_paired_deltas():
    before = {"q1": 0.1, "q2": 0.5, "q3": 0.5}
    after = {"q1": 0.3, "q2": 0.5, "q3": 0.2}
    d = gold_metrics.paired_deltas(before, after)
    assert d["improved"] == 1
    assert d["non_worse"] == 2  # q1 up, q2 equal
    assert d["worsened"] == 1
    assert d["per_query"]["q3"] == -0.3
