"""Pure serialization/aggregation helpers for battery_sweep (spec §5 PR 1).

Kept free of I/O and stack access so the JSON contract every later PR diffs
against is unit-tested (the sweep script itself needs the live stack).
"""

from __future__ import annotations

from statistics import median
from typing import Any


def latency_summary(ms_all: list[float]) -> dict[str, float]:
    """Median + IQR over repeated wall-clock samples (spec §3.5)."""
    ordered = sorted(ms_all)
    n = len(ordered)
    q1 = ordered[max(0, (n // 4))]
    q3 = ordered[min(n - 1, (3 * n) // 4)]
    return {"ms_median": float(median(ordered)), "ms_iqr": float(q3 - q1)}


def serialize_run(
    rows: list[dict],
    ground_truth: dict[str, dict],
    binding: dict[str, Any],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Build the JSON-serializable run document (sets become sorted lists)."""
    out_rows = []
    for c in rows:
        row = {"edge_budget": c["edge_budget"], "top_k": c["top_k"], "dedup": c["dedup"], "q": {}}
        for name, q in c["q"].items():
            entry = dict(q)
            ms_all = entry.pop("ms_all", None)
            if ms_all:
                entry.update(latency_summary(ms_all))
            row["q"][name] = entry
        out_rows.append(row)
    gts = {
        name: {**gt, "gt_ids": sorted(gt["gt_ids"]), "pool_preds": sorted(gt["pool_preds"])}
        for name, gt in ground_truth.items()
    }
    return {"binding": binding, **meta, "rows": out_rows, "ground_truth": gts}
