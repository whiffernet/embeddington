#!/usr/bin/env python3
"""Judge-vs-referee agreement on the validation sample (spec §3.3 gate)."""

from __future__ import annotations

import json
import re
from pathlib import Path

GOLD = Path(__file__).resolve().parent
BAR = 0.80  # precision-on-relevant acceptance bar


def main() -> None:
    labels = json.loads((GOLD / "labels.json").read_text())
    rows = []
    for line in (GOLD / "VALIDATION_SAMPLE.md").read_text().splitlines():
        m = re.match(
            r"\|\s*\d+\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|.*\|\s*(relevant|marginal|irrelevant)\s*\|$",
            line,
        )
        if m:
            rows.append(m.groups())
    if len(rows) < 25:
        raise SystemExit(f"only {len(rows)} filled rows parsed — is VALIDATION_SAMPLE.md complete?")
    tp = sum(1 for n, e, h in rows if labels[n][e]["label"] == "relevant" and h == "relevant")
    fp = sum(1 for n, e, h in rows if labels[n][e]["label"] == "relevant" and h != "relevant")
    fn = sum(1 for n, e, h in rows if labels[n][e]["label"] != "relevant" and h == "relevant")
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    verdict = (
        "ACCEPTED" if precision >= BAR else "REJECTED — revise labels (Task 8) and re-validate"
    )
    out = (
        f"# Judge validation (spec §3.3)\n\n"
        f"Sample: {len(rows)} referee-labeled edges (VALIDATION_SAMPLE.md, seed 47).\n\n"
        f"| metric | value |\n|---|---|\n"
        f"| judge precision on `relevant` | {precision:.2f} |\n"
        f"| judge recall on `relevant` | {recall:.2f} |\n"
        f"| acceptance bar | precision ≥ {BAR:.2f} |\n"
        f"| verdict | **{verdict}** |\n"
    )
    # Writes to a metrics side-file: JUDGE-VALIDATION.md is a hand-written history
    # doc (method decisions, tiebreak record) and must never be machine-overwritten.
    (GOLD / "JUDGE-VALIDATION-metrics.md").write_text(out, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
