# Baseline (pre-#36) gold scores — shipped default eb=40 k=5 dedup=on

Sweep: `2026-07-18-baseline-pre36-sweep.json` (binding baseline-2026-07b, git fc0d1ba, reps 5).

| query | gold_recall@budget | gold_precision | n_relevant |
|---|---|---|---|
| case1_realistic_3hint | 0.000 | 0.000 | 10 |
| case2_minimal | 0.077 | 0.040 | 13 |
| hub_cmdb_rel_ci | 0.000 | 0.000 | 14 |
| hub_process_mining | 0.091 | 0.045 | 11 |
| hub_discovery | 0.214 | 0.120 | 14 |
| hub_cmdb | 0.071 | 0.034 | 14 |
| hub_incident | 0.000 | 0.000 | 7 |
| hub_predictive_intelligence | 0.118 | 0.091 | 17 |
| control_no_hints_snake | 0.000 | 0.000 | 6 |
| control_predicate_filter | 0.857 | 0.343 | 14 |
| control_multifacet_license | 0.000 | 0.000 | 1 |

**Mean gold-recall@budget (baseline selector): 0.130** over 11 scoreable queries.
