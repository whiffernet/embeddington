# PR 3 evidence — relevance-aware edge selection (#36)

Final measured run: `battery_results/2026-07-19-pr3-final-sweep.{md,json}` — full 30-combo
grid, `SWEEP_REPS=5`, shipped defaults (`DIVERSITY_QUOTA_FRACTION=0.40`, no env
overrides), restored battery stack (binding `baseline-2026-07b`, verified by the sweep's
hard-fail gate). Baseline comparator: `battery_results/2026-07-18-baseline-pre36-sweep.json`.
Evaluator: `compare_to_baseline.py` (amended floor — see gold/README.md Amendment).

## Gate verdict (exit 0)

| criterion                                          | value        | verdict       |
| -------------------------------------------------- | ------------ | ------------- |
| mean gold-recall >= 0.280                          | 0.283        | PASS          |
| >=9/11 non-worse                                   | 9            | PASS          |
| >=6 improved                                       | 7            | PASS          |
| c1 > 0.00                                          | 0.200        | PASS          |
| hub_process_mining improved                        | 0.091->0.455 | PASS          |
| hub_incident improved                              | 0.000->0.429 | PASS          |
| per-predicate recall >= 0.80 (mean across queries) | 0.977        | PASS          |
| per-predicate recall (worst query, informational)  | 0.750        | --            |
| hub_discovery improved                             | 0.214->0.143 | DEFERRED(PR4) |
| hub_cmdb improved                                  | 0.071->0.071 | DEFERRED(PR4) |

Baseline selector mean gold-recall was **0.130** (BASELINE.md) — the shipped selector
returns **2.2×** the gold-relevant edges, and revives four formerly-zero queries
(`c1` 0→0.200, `hub_incident` 0→0.429, `control_no_hints_snake` 0→0.333,
`hub_cmdb_rel_ci` 0→0.071).
Mean gold-precision (watched, no gate): 0.126 (baseline 0.061).

## Quota-fraction arm sweep (reps=1, shipped combo)

| arm     | mean gold-recall | worst-query per-pred | mean per-pred | verdict vs original 9-line floor      |
| ------- | ---------------- | -------------------- | ------------- | ------------------------------------- |
| 0.20    | 0.277            | 0.500                | —             | FAIL (mean, per-pred, non-worse 8/11) |
| 0.25    | 0.283            | 0.583                | 0.909         | FAIL (per-pred worst, disc, cmdb)     |
| 0.30    | 0.283            | 0.667                | —             | FAIL (per-pred worst, disc, cmdb)     |
| 0.40 ⭐ | 0.283            | 0.750                | 0.977         | PASS amended floor                    |
| 0.50    | 0.283            | 0.750                | —             | identical to 0.40                     |

Findings: mean gold-recall is **quota-insensitive** in 0.25–0.50 (relevance ranking
places gold edges at the top of both selection phases; the fraction only reshuffles the
low-relevance tail). Worst-query per-predicate is **token-ceiling-capped** on `c1`
(~16 post-trim edges across 5 concepts spanning 12 predicate types) — it plateaus at
0.750 regardless of quota. **0.40 shipped**: best diversity at zero recall cost.

## Floor amendment (maintainer decision, 2026-07-19)

Two of the original nine pinned lines were amended — recorded in full in
`gold/README.md` (Amendment section), never silently:

1. per-predicate floor evaluated as **mean across queries** (0.977 ≥ 0.80); worst-query
   kept as an informational row with the ceiling-cap analysis.
2. `hub_discovery`/`hub_cmdb` improvement **deferred to PR 4's gate**: judge–cosine AUC
   on those pools is 0.683/0.432 (`independence.json`) — cosine relevance is nearly
   random for cmdb, so a cosine-ranking selector cannot reliably lift them. This is the
   measured form of issue #38's thesis; the lexical lane is the designed fix.

## Latency (the honest cost)

Cold enrich at shipped combo, sweep-measured (reps=5 median-of-medians, CPU embed
sidecar): **188 ms → 12,207 ms** (~45 ms/quote × up to ~300 pool quotes batch-embedded
per call). Flagged per the plan's >2× rule; maintainer decision: ship with an
**in-process LRU quote-embedding cache** (quotes are stable KG content; identical
vectors, so selection semantics and every quality number above are unaffected).

Cache demonstration (battery stack, 11 queries run twice in one process, shipped combo):
**cold median 5,558 ms** (pool overlap already amortizes) → **warm median 201 ms** —
back to the pre-change baseline. GPU-backed embed services absorb most of the cold cost
as well. Queued follow-up: precompute/persist quote embeddings at install/update so
first calls are also fast.

Degradation-path integrity: the final run logged 19 `relevance scoring unavailable`
warnings, all in `eb=120` grid cells (large-pool batch embeds timing out on CPU
— noted for PR 6's re-tune); the shipped-combo segment is **clean**, so every gated
number above was measured with relevance scoring active.

## Continuity note

The sweep's legacy cosine-retention column now partially reflects the selector's own
signal and is **context only** — gold-recall (cross-family majority labels, AUC 0.614
independence) is the gate, per spec §3.
