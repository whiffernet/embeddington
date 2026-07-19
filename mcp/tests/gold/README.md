# Gold set — frozen relevance ground truth (issue #46, spec §3)

Frozen against `baseline-2026-07b` (binding in pools.json; drift hard-fails).
Files: pools.json (candidate pools), labels.json (judge labels),
PROTOCOL.md (prompts), JUDGE-VALIDATION.md (cross-family 2-of-3 majority
construction — Claude judge / gpt-oss-120b / llama-3.3-70b, 121 relevant of
2,765 pool edges; see that file for full history including the round-1
single-referee rejection), independence.json (judge-vs-cosine AUC on the
majority labels, mean 0.614 — healthy independence: bge-m3 cosine only
weakly predicts gold-relevance, so a cosine-ranking selector cannot
mechanically saturate gold-recall; no tautology discount needed),
BASELINE.md (pre-#36 baseline selector scores).

## PR 3 (#36) acceptance floor — FIXED BEFORE PR 3 BEGINS (spec §5 PR 1)

Baseline mean gold-recall@budget = **0.130** (BASELINE.md, shipped default
`edge_budget=40, top_k=5, dedup=on`). PR 3 merges only if, at the shipped
default combo, ALL of:

1. mean gold-recall@budget ≥ **0.280** (0.130 + 0.15)
2. ≥ 9 of 11 queries non-worse AND ≥ 6 improved (gold_metrics.paired_deltas)
3. c1 (case1_realistic_3hint) gold-recall > 0.00
4. hub queries (procmin, disc, cmdb, incid) each improved
5. per-predicate recall ≥ 0.80
6. per-edge grounding fields intact (existing regression tests)

Neither line degenerates at these measured numbers (0.130 + 0.15 = 0.280,
well under 1.0), so no adjustment is recorded.

`control_multifacet_license` has only 1 gold-relevant edge, so its
gold-recall@budget is coarse — 0.000 or 1.000, nothing between. It still
counts toward line 2's non-worse/improved tally as committed above (the
formula stays the single implementable rule); readers scoring line 2 by
hand should treat a flip on that query as a weaker signal than the other
10 queries' continuous deltas.

## Baseline sweep note — do not misread the printed knee

The pre-#36 sweep (`../battery_results/2026-07-18-baseline-pre36-sweep.md`)
prints `CHANGE (40,5)->(60,5)` as its retention-knee finding. That finding
is **informational only** for this task: it does not change the shipped
defaults used above, and PR 1 does not re-tune `edge_budget`/`top_k`.
Re-tuning is scoped to PR 6, after the selection fix (PR 3) lands.

## Rebuild protocol

Rebuild (capture_pools.py -> Task-8 labeling -> validation -> this README)
whenever the battery baseline changes or pools.json fingerprints drift.
