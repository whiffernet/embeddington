#!/usr/bin/env python3
"""PR 3 gate evaluation (spec §5 PR 3 + the pinned floor in gold/README.md).

Usage: compare_to_baseline.py <new-sweep.json> <baseline-sweep.json>
Scores both sweeps' shipped-combo rows against the frozen labels and prints
each pinned criterion with PASS/FAIL. Exit 0 iff ALL criteria pass.
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
HUBS = ["hub_process_mining", "hub_discovery", "hub_cmdb", "hub_incident"]


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
    for h in HUBS:
        checks.append(
            (
                f"{h} improved",
                deltas["per_query"].get(h, -1) > 0,
                f"{base_r.get(h, 0):.3f}->{new_r.get(h, 0):.3f}",
            )
        )
    worst_pp = min(new_pp.values())
    checks.append(
        ("per-predicate recall >= 0.80 (worst query)", worst_pp >= 0.80, f"{worst_pp:.3f}")
    )
    print("| criterion | value | verdict |\n|---|---|---|")
    ok = True
    for name, passed, val in checks:
        ok &= passed
        print(f"| {name} | {val} | {'PASS' if passed else 'FAIL'} |")
    print(f"\nmean gold-precision (watched, no gate): {sum(new_p.values()) / len(new_p):.3f}")
    print(
        f"per-query recall deltas: "
        f"{json.dumps({k: round(v, 3) for k, v in sorted(deltas['per_query'].items())})}"
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
