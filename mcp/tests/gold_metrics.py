"""Gold-set metrics (spec §3.4/3.5).

``gold_recall_at_budget`` deliberately normalizes by ``|gold-relevant|`` — a
budget-independent constant per query — so the tuned parameter never appears
in the denominator of the metric that judges the tuning (critic finding F1).
"""

from __future__ import annotations


def gold_recall_at_budget(kept_ids: set[str], relevant_ids: set[str]) -> float | None:
    """Fraction of gold-relevant edges the selector kept.

    Args:
        kept_ids: Edge ids the tool returned.
        relevant_ids: Edge ids labeled ``relevant`` in the frozen gold set.

    Returns:
        Recall in [0, 1], or None when the query has no relevant edges
        (excluded from battery means rather than counted as 0 or 1).
    """
    if not relevant_ids:
        return None
    return len(kept_ids & relevant_ids) / len(relevant_ids)


def gold_precision(kept_ids: set[str], relevant_ids: set[str]) -> float | None:
    """Fraction of kept edges that are gold-relevant (None when kept empty)."""
    if not kept_ids:
        return None
    return len(kept_ids & relevant_ids) / len(kept_ids)


def paired_deltas(before: dict[str, float], after: dict[str, float]) -> dict:
    """Per-query paired comparison over the keys present in both runs."""
    per_query = {q: after[q] - before[q] for q in before.keys() & after.keys()}
    return {
        "improved": sum(1 for d in per_query.values() if d > 0),
        "non_worse": sum(1 for d in per_query.values() if d >= 0),
        "worsened": sum(1 for d in per_query.values() if d < 0),
        "per_query": per_query,
    }
