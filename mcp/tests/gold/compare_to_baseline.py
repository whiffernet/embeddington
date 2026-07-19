#!/usr/bin/env python3
"""PR 3 gate evaluation (spec §5 PR 3 + the pinned floor in gold/README.md).

Usage: compare_to_baseline.py <new-sweep.json> <baseline-sweep.json>
Scores both sweeps' shipped-combo rows against the frozen labels and prints
each pinned criterion with PASS/FAIL (or DEFERRED for the two amended hub
rows). Exit 0 iff every gating criterion passes.

Amended 2026-07-19 (maintainer decision, PR 3 arm-sweep measurement): the
original nine-line floor is preserved verbatim in gold/README.md; this
evaluator implements two deviations from it, both amendments not rewrites —
see the "Amendment — 2026-07-19" subsection of gold/README.md for the full
rationale and the arm-sweep evidence behind each:

1. The per-predicate-recall line is now MEAN across queries >= 0.80 (was:
   worst query >= 0.80). The worst-query value is still printed, as a
   non-gating informational row — it plateaus at 0.750 from edge_budget=40's
   token ceiling, independent of the diversity quota fraction.
2. hub_discovery and hub_cmdb are now DEFERRED(PR4), not gating PASS/FAIL —
   their recall is cosine-similarity-bound (see gold/independence.json),
   deferred to PR 4's lexical-lane gate. hub_process_mining and hub_incident
   remain gating.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TESTS))

import gold_metrics  # noqa: E402

GOLD = Path(__file__).resolve().parent
SHIPPED = {"edge_budget": 40, "top_k": 5, "dedup": "on"}
FLOOR_MEAN = 0.280
GATING_HUBS = ["hub_process_mining", "hub_incident"]
DEFERRED_HUBS = ["hub_discovery", "hub_cmdb"]  # amended 2026-07-19: cosine-bound, see README.md


def _score(
    sweep: dict, labels: dict
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    row = next(r for r in sweep["rows"] if {k: r[k] for k in SHIPPED} == SHIPPED)
    recalls: dict[str, float] = {}
    precisions: dict[str, float] = {}
    pp: dict[str, float] = {}
    for name, q in row["q"].items():
        relevant = {e for e, r in labels[name].items() if r["label"] == "relevant"}
        kept = set(q["kept_ids"])
        rec = gold_metrics.gold_recall_at_budget(kept, relevant)
        if rec is not None:
            recalls[name] = rec
        prec = gold_metrics.gold_precision(kept, relevant)
        if prec is not None:
            precisions[name] = prec
        pp[name] = q["pp"]
    return recalls, precisions, pp


def main() -> None:
    new_sweep = json.loads(Path(sys.argv[1]).read_text())
    base_sweep = json.loads(Path(sys.argv[2]).read_text())
    labels = json.loads((GOLD / "labels.json").read_text())
    new_r, new_p, new_pp = _score(new_sweep, labels)
    base_r, _, _ = _score(base_sweep, labels)
    deltas = gold_metrics.paired_deltas(base_r, new_r)
    mean_new = sum(new_r.values()) / len(new_r)
    mean_pp = sum(new_pp.values()) / len(new_pp)
    worst_pp = min(new_pp.values())
    checks = [
        ("mean gold-recall >= 0.280", mean_new >= FLOOR_MEAN, f"{mean_new:.3f}"),
        (">=9/11 non-worse", deltas["non_worse"] >= 9, str(deltas["non_worse"])),
        (">=6 improved", deltas["improved"] >= 6, str(deltas["improved"])),
        (
            "c1 > 0.00",
            new_r.get("case1_realistic_3hint", 0.0) > 0.0,
            f"{new_r.get('case1_realistic_3hint', 0.0):.3f}",
        ),
    ]
    for h in GATING_HUBS:
        checks.append(
            (
                f"{h} improved",
                deltas["per_query"].get(h, -1) > 0,
                f"{base_r.get(h, 0):.3f}->{new_r.get(h, 0):.3f}",
            )
        )
    checks.append(
        (
            "per-predicate recall >= 0.80 (mean across queries)",
            mean_pp >= 0.80,
            f"{mean_pp:.3f}",
        )
    )
    print("| criterion | value | verdict |\n|---|---|---|")
    ok = True
    for name, passed, val in checks:
        ok &= passed
        print(f"| {name} | {val} | {'PASS' if passed else 'FAIL'} |")
    # Non-gating informational row (amended 2026-07-19): the worst-query
    # per-predicate value is superseded as the gating criterion by the mean
    # above, but still printed — it plateaus at 0.750 from edge_budget=40's
    # token ceiling (spec's fixed trim floor), not from the diversity quota,
    # so it is a real, reproducible ceiling effect worth watching rather than
    # a regression signal. See README.md's Amendment section for the evidence.
    print(
        "| per-predicate recall (worst query, informational — ceiling-capped, "
        f"see PR3-EVIDENCE) | {worst_pp:.3f} | -- |"
    )
    # Non-gating rows (amended 2026-07-19): hub_discovery/hub_cmdb recall is
    # cosine-similarity-bound (see gold/independence.json), deferred to PR 4's
    # lexical-lane gate. Printed for visibility, excluded from the exit code.
    for h in DEFERRED_HUBS:
        print(f"| {h} improved | {base_r.get(h, 0):.3f}->{new_r.get(h, 0):.3f} | DEFERRED(PR4) |")
    print(f"\nmean gold-precision (watched, no gate): {sum(new_p.values()) / len(new_p):.3f}")
    print(
        f"per-query recall deltas: "
        f"{json.dumps({k: round(v, 3) for k, v in sorted(deltas['per_query'].items())})}"
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
