#!/usr/bin/env python3
"""Baseline selector's per-query gold-recall/precision (spec §3.5).

Reads a sweep JSON (kept_ids per combo/query) + the frozen labels, scores the
SHIPPED default combo (edge_budget=40, top_k=5, dedup=on), and writes
BASELINE.md. Usage: compute_gold_baseline.py <path-to-sweep.json>
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


def main() -> None:
    sweep = json.loads(Path(sys.argv[1]).read_text())
    labels = json.loads((GOLD / "labels.json").read_text())
    row = next(r for r in sweep["rows"] if {k: r[k] for k in SHIPPED} == SHIPPED)
    lines = [
        "# Baseline (pre-#36) gold scores — shipped default eb=40 k=5 dedup=on",
        "",
        f"Sweep: `{Path(sys.argv[1]).name}` (binding {sweep['binding']['baseline']}, "
        f"git {sweep['git_sha']}, reps {sweep['reps']}).",
        "",
        "| query | gold_recall@budget | gold_precision | n_relevant |",
        "|---|---|---|---|",
    ]
    recalls: dict[str, float] = {}
    for name, q in row["q"].items():
        relevant = {e for e, r in labels[name].items() if r["label"] == "relevant"}
        kept = set(q["kept_ids"])
        rec = gold_metrics.gold_recall_at_budget(kept, relevant)
        prec = gold_metrics.gold_precision(kept, relevant)
        if rec is not None:
            recalls[name] = rec
        lines.append(
            f"| {name} | {'-' if rec is None else f'{rec:.3f}'} | "
            f"{'-' if prec is None else f'{prec:.3f}'} | {len(relevant)} |"
        )
    mean = sum(recalls.values()) / len(recalls)
    lines += [
        "",
        f"**Mean gold-recall@budget (baseline selector): {mean:.3f}** "
        f"over {len(recalls)} scoreable queries.",
    ]
    (GOLD / "BASELINE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
