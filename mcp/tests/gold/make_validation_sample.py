#!/usr/bin/env python3
"""Build the 30-edge referee validation sample (spec §3.3), seeded + stratified.

Labeled by an independent cross-model referee (see JUDGE-VALIDATION.md for the
method decision); the referee_label column must be filled blind to labels.json.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

GOLD = Path(__file__).resolve().parent
SEED = 47  # round-2 fresh draw after the cross-family label revision (round 1 used 46)
N_TOTAL = 30
MUST_COVER = ["hub_process_mining", "hub_discovery", "hub_cmdb", "hub_incident"]


def main() -> None:
    pools = json.loads((GOLD / "pools.json").read_text())
    labels = json.loads((GOLD / "labels.json").read_text())
    rng = random.Random(SEED)
    picks: list[tuple[str, str]] = []
    # 4 from each hub query (2 judged relevant / 2 not, where available)...
    for name in MUST_COVER:
        rel = [e for e, r in labels[name].items() if r["label"] == "relevant"]
        non = [e for e, r in labels[name].items() if r["label"] != "relevant"]
        picks += [(name, e) for e in rng.sample(rel, min(2, len(rel)))]
        picks += [(name, e) for e in rng.sample(non, min(2, len(non)))]
    # ...fill to 30 from the remaining queries, stratified by label.
    others = [n for n in labels if n not in MUST_COVER]
    flat = [(n, e, labels[n][e]["label"]) for n in others for e in labels[n]]
    rng.shuffle(flat)
    for lab in ("relevant", "marginal", "irrelevant"):
        for n, e, lbl in flat:
            if len(picks) >= N_TOTAL:
                break
            if lbl == lab and (n, e) not in picks:
                picks.append((n, e))
    lines = [
        "# Referee validation sample — fill the `referee_label` column",
        "",
        "Labels: relevant | marginal | irrelevant (see PROTOCOL.md defs).",
        "Judge only the quote against the query, blind to labels.json.",
        "",
        "| # | query_name | edge_id | predicate | source_quote | referee_label |",
        "|---|---|---|---|---|---|",
    ]
    for i, (name, eid) in enumerate(picks[:N_TOTAL], 1):
        ed = pools["queries"][name]["edges"][eid]
        quote = (ed.get("source_quote") or "(no quote)").replace("|", "\\|")
        lines.append(f"| {i} | {name} | {eid} | {ed.get('predicate')} | {quote} |  |")
    (GOLD / "VALIDATION_SAMPLE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote VALIDATION_SAMPLE.md with {min(N_TOTAL, len(picks))} rows")


if __name__ == "__main__":
    main()
