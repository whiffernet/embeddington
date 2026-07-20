#!/usr/bin/env python3
"""Issue #37 monotonicity check (spec §5 PR 6, issue #44).

#37's acceptance criterion: raising ``edge_budget`` must never decrease mean
gold-recall@budget. Reads a fixed-11-cohort sweep JSON + the frozen labels,
scores every ``edge_budget`` at ``top_k=5, dedup=on`` against the
budget-independent gold-recall metric (the same metric ``compare_to_baseline.py``
uses for the single shipped-combo row, generalized across the full
``edge_budget`` curve), and reports whether the curve is non-decreasing
within a small negative-noise tolerance.

Usage: compute_monotonicity.py <path-to-fixed-cohort-sweep.json> [tolerance]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TESTS))

import gold_metrics  # noqa: E402

GOLD = Path(__file__).resolve().parent
EDGE_BUDGETS = [20, 40, 60, 80, 120]
TOP_K = 5
DEFAULT_TOLERANCE = 0.005  # absolute; any decrease beyond this fails #37


def _mean_gold_recall(sweep: dict, labels: dict, edge_budget: int) -> float:
    row = next(
        r
        for r in sweep["rows"]
        if r["edge_budget"] == edge_budget and r["top_k"] == TOP_K and r["dedup"] == "on"
    )
    recalls = []
    for name, q in row["q"].items():
        relevant = {e for e, r in labels[name].items() if r["label"] == "relevant"}
        kept = set(q["kept_ids"])
        rec = gold_metrics.gold_recall_at_budget(kept, relevant)
        if rec is not None:
            recalls.append(rec)
    return sum(recalls) / len(recalls)


def main() -> None:
    """Print the mean gold-recall@budget curve and the #37 monotonicity verdict.

    Args:
        sys.argv[1]: Path to a fixed-11-cohort sweep JSON.
        sys.argv[2]: Optional absolute decrease tolerance (default 0.005).

    Exits 0 if the curve is non-decreasing within tolerance across the
    full ``EDGE_BUDGETS`` grid, 1 otherwise (mirroring the other gold/
    evaluator scripts' exit-code convention).
    """
    sweep = json.loads(Path(sys.argv[1]).read_text())
    tolerance = float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_TOLERANCE
    labels = json.loads((GOLD / "labels.json").read_text())

    means = {eb: _mean_gold_recall(sweep, labels, eb) for eb in EDGE_BUDGETS}

    print(f"#37 monotonicity — mean gold-recall@budget at top_k={TOP_K}, dedup=on")
    print(f"Sweep: `{Path(sys.argv[1]).name}` (tolerance {tolerance})")
    print()
    print("| edge_budget | mean_gold_recall | delta vs prior | verdict |")
    print("|---|---|---|---|")
    prior_eb: int | None = None
    met_through = EDGE_BUDGETS[0]
    fully_met = True
    for eb in EDGE_BUDGETS:
        mean = means[eb]
        if prior_eb is None:
            delta_str, verdict = "-", "-"
        else:
            delta = mean - means[prior_eb]
            non_decreasing = delta >= -tolerance
            delta_str = f"{delta:+.3f}"
            verdict = "OK" if non_decreasing else "DECREASE"
            if non_decreasing:
                # Only advance while still unbroken — a later OK after an
                # earlier DECREASE must not resurrect met_through past the
                # break (that would misreport a curve with a dip as
                # non-decreasing "through" a point beyond the dip).
                if fully_met:
                    met_through = eb
            else:
                fully_met = False
        print(f"| {eb} | {mean:.3f} | {delta_str} | {verdict} |")
        prior_eb = eb

    print()
    if fully_met:
        print(f"#37 MET: non-decreasing across all of {EDGE_BUDGETS}.")
    else:
        print(
            f"#37 PARTIALLY MET: non-decreasing through edge_budget={met_through}, "
            f"decreases beyond it."
        )
    sys.exit(0 if fully_met else 1)


if __name__ == "__main__":
    main()
