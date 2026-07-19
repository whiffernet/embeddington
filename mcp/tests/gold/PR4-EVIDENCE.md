# PR 4 evidence — hybrid retrieval + score threshold (#38)

Final measured runs: `battery_results/2026-07-19-pr4-final-sweep.{md,json}` (fixed-11
cohort) and `battery_results/2026-07-19-pr4-final-identifier-sweep.{md,json}` (identifier
cohort) — full grids, `SWEEP_REPS=5`, shipped defaults (`EMBEDDINGTON_SCORE_THRESHOLD=0.50`,
lexical lane depth `max(top_k*2, 25)`, quota 0.40), binding-verified battery stack.
Threshold arms: `battery_results/pr4-thr-{0.0,0.50,0.55}-*`. Comparators: PR 3 final
(`2026-07-19-pr3-final-sweep.json`) and the pre-#36 baseline
(`2026-07-18-baseline-pre36-sweep.json`). Zero `relevance scoring failed` warnings in any
final or arm run log.

## Gate verdicts

| gate                                                    | result                                                             | verdict                                   |
| ------------------------------------------------------- | ------------------------------------------------------------------ | ----------------------------------------- |
| identifier cohort: literal-match chunks in fused top-5  | 4/4 queries (live, shipped defaults)                               | PASS                                      |
| no-good-match returns < top_k                           | nonsense probe: 5 padded chunks @thr 0.0 → **0** @0.50             | PASS                                      |
| fixed-11 gold-recall non-regression vs PR 3 (paired)    | 10/11 non-worse; mean 0.283→0.268                                  | PASS (single explained dip, below)        |
| post-restore degraded mode                              | unit + live: `building`/`absent` ⇒ lexical skipped + exact warning | PASS                                      |
| per-predicate (mean, amended floor)                     | 0.977 unchanged                                                    | PASS                                      |
| hub_discovery improved vs baseline (deferred from PR 3) | 0.214 → 0.143                                                      | **FAILED — measured limit, see analysis** |
| hub_cmdb improved vs baseline (deferred from PR 3)      | 0.071 → 0.071                                                      | **FAILED — measured limit, see analysis** |

Identifier-cohort KG-side scores (secondary; the cohort's primary gate is the vector
lane): `id_pm_project` gold-recall 0.286 / pp 1.00, `id_sc_cat_item` 0.500 / pp 1.00;
`id_disc_plugin`/`id_mim_plugin` have **empty KG pools** (deficiency below). Warm-cache
latency at shipped combo: 104–187 ms median across the cohort.

## Threshold selection (measured, not vibes)

Score distributions on the live corpus: nonsense probes top out at **~0.45**
("purple elephant…" 0.439, sourdough 0.451); the weakest legitimate battery query
bottoms at **~0.56** (`sc_req_item` tail 0.564). **0.50 shipped** — margin on both
sides. Arms {0.0, 0.50, 0.55} were IDENTICAL on every fixed-11 KG metric (all
legitimate scores clear even 0.55), differing only on the nonsense probe (5→0 chunks).
0.55 was rejected as too close to the legitimate tail.

## The single fixed-11 dip (honest accounting)

`control_no_hints_snake` ("What is the sc_req_item table?") dropped 0.333→0.167
gold-recall (25→19 kept edges). Cause: the query itself carries an identifier, so the
new lexical lane changed its fused chunk mix; the different chunk token footprint made
the response-ceiling trim pop 6 more KG edges, one of which was gold-relevant. The same
query's _vector_ lane now surfaces literal `sc_req_item` chunks it previously missed —
the KG-side dip buys a vector-side gain the KG metric can't see. Every other query is
unchanged or improved vs PR 3.

## Deferred-hub verdicts: FAILED, and why (maintainer-accepted, 2026-07-19)

PR 3 deferred `hub_discovery`/`hub_cmdb` improvement to this PR on the thesis that the
lexical lane was the designed fix. **Measured result: the thesis was wrong for these two
queries.** Their phrasing ("Explain Discovery/CMDB in ServiceNow.") contains no
identifier tokens, so the lexical lane never fires for them — PR 4's mechanism
structurally cannot touch them. Combined with PR 3's finding (judge–cosine AUC 0.683/0.432
on their pools — cosine relevance near-random for cmdb), the honest conclusion is:
**hub-entity queries need a relevance signal that neither dense cosine nor lexical
matching provides** — a reranker with a different modality, or richer KG-side structure
from the ontology/extraction round (#43). Closed as a measured limit by maintainer
decision; follow-up filed (see the issue referenced in the PR body). Nothing about this
is hidden: both lines are recorded FAILED here, not re-deferred.

## Measured deficiency: dotted-identifier blindness in KG hint extraction

`id_disc_plugin` ("com.snc.discovery") and `id_mim_plugin` ("com.snc.incident.mim")
pool **zero KG edges**: `_extract_entity_hints` cannot resolve dotted identifiers, so
the KG side never seeds entities for them (entities like
`plugin__discovery_com.snc.discovery` exist in the graph). The vector lane covers these
queries (4/4 literal hits above); the KG blindness is pinned by
`test_identifier_empty_pools_pinned_to_plugin_deficiency` — any future hint-extraction
fix must deliberately re-capture and re-label the cohort. Evidence for the
extraction/ontology round.

## Lexical lane mechanics (live-validated)

Qdrant's `word` tokenizer splits on underscores/dots, so MatchText is subtoken-AND, not
exact-match; the lane therefore post-filters to chunks containing the **literal** token
(case-insensitive) before RRF. Common-subtoken identifiers (`pm_project` → {pm, project})
drown in the filtered set — literal chunks first appeared at filtered-dense ranks 14–23 —
so the lane fetches `max(top_k*2, 25)` deep. Both behaviors carry regression tests. The
`chunk_text` field + index are consumer-local (materialization: 152,191/152,194 points in
3m30s on the battery stack; 3 no-prose points excluded by design); a baseline restore
sheds them and the every-start ensure + consumer warm-up recreate them; index
registration races are ridden out by a short post-create poll.

## Continuity note

Cosine-retention columns in the sweeps remain context-only; gold-recall against the
frozen cross-family labels is the gate (spec §3). The identifier cohort is scored
separately from the fixed-11 and never blended (spec §3.4).
