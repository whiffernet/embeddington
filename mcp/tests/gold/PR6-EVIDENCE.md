# PR 6 evidence — response-ceiling re-tune + #37 close-out (#44)

Final measured runs: `battery_results/2026-07-20-pr6-final-sweep.{md,json}`
(fixed-11 cohort) and `battery_results/2026-07-20-pr6-final-identifier-sweep.{md,json}`
(identifier cohort) — full grid (`edge_budget ∈ {20,40,60,80,120}` × `top_k
∈ {3,5,10}` × `dedup ∈ {on,off}`), `SWEEP_REPS=5`, shipped-at-measurement-time
defaults (`edge_budget=40, top_k=5`), v0.8.0 battery stack (qdrant :19411 /
152,194 pts, arango :19412 / 683,651 edges — NOT prod). Both worst-response
calibration dumps (`*-worst-response.json` ×2) are committed alongside. Zero
`relevance scoring failed` degradation warnings in either run log.

## 1. Calibration: `estimate_tokens` vs a real tokenizer

`mcp/tests/gold/calibrate_tokens.py` computes `estimate_tokens` (the ÷3
heuristic `budget.py` uses to police the response ceiling) against
`tiktoken`'s `cl100k_base` encoding (a documented proxy — Claude's own
tokenizer is not public) over every committed `*-worst-response.json` dump.
Full per-file table in `mcp/tests/gold/token_calibration.json`; summary:

| group (ratio = est/real)                                                        | files | est         | real        |
| ------------------------------------------------------------------------------- | ----- | ----------- | ----------- |
| 1.263 (baseline-pre36, pr3-final, pr4-final, pr3-arm ×5, pr4-thr ×3, pr6-final) | 12    | 12000–12013 | 9501–9513   |
| 1.195 (pr4-final-identifier, pr4-thr ×3-identifier, pr6-final-identifier)       | 5     | 11998–12011 | 10041–10053 |

**Finding: `estimate_tokens` overestimates on all 17 dumps (ratio 1.195–1.263,
never < 1.0) — it never underestimates.** So `e = max(0, max(1 − ratio)) =
0.0`, and the calibrated bar `int(9000 × (1 − e)) = 9000` — unchanged from
the nominal bar. Real worst-case responses run ~9.5k–10k real (cl100k)
tokens against a 12,000-estimated-token ceiling; the heuristic's own
`TOKEN_DIVISOR = 3` comment ("deliberately pessimistic") is confirmed
correct in direction — most BPE tokenizers land closer to ~4 chars/token on
JSON-like text than 3. The two `pr6-final` dumps (this PR's own final runs,
committed in `78938ca`) were added to the corpus at HEAD and re-ran the
same calibration (`e=0.0`, bar unchanged) — the finding is not sensitive to
which worst-case dumps are in the fixed corpus.

## 2. Headroom vs the calibrated bar (9000 est-tokens)

Worst-case estimated tokens per `edge_budget` at `top_k=5, dedup=on` (from
the final fixed-11 sweep; see the sweep `.md` for the full 15-combo grid
across all three `top_k` values):

| edge_budget | worst est-tokens (fixed-11) | worst est-tokens (identifier) | ≤ 9000? |
| ----------- | --------------------------- | ----------------------------- | ------- |
| 20          | 12003                       | 11667                         | no      |
| 40          | 11987                       | 12002                         | no      |
| 60          | 12011                       | 11962                         | no      |
| 80          | 12013                       | 11956                         | no      |
| 120         | 11934                       | 12011                         | no      |

**Finding 1 (reproduced from the PR 1 sweep, calibrated numbers restated):
0/15 `dedup=on` combos in the full grid land ≤ 9000 estimated tokens** —
across the full grid (both cohorts, both `dedup` settings), worst-case
tokens range **~10,300–12,013**; restricting to `edge_budget ≥ 40` (the
range that matters for this decision) it tightens to **≥11,900**,
essentially the 12,000 ceiling regardless of `top_k`. The low end of the
wider range (~10,300) comes from `edge_budget=20`'s smaller allocation on
the identifier cohort, not from any combo actually being close to the
9000-token bar. Real RAG chunks (~2k tokens each) plus rich KG edges
(~260 tokens each) saturate the ceiling at `edge_budget ≥ 40`; the
response-ceiling trim fills to just under it no matter what the caller
asks for, so the ≥25%-headroom bar (≤9000 est-tokens) cannot discriminate
between grid points there — it isn't wired to `edge_budget`/`top_k` at
all.

**Amended bar (maintainer-authorized restatement of PR 1's Finding-1, not a
new deviation):** headroom is a _ceiling_ / chunk-size lever
(`EMBEDDINGTON_MAX_RESPONSE_TOKENS`, `source_quote`/text length), not an
`edge_budget`/`top_k` lever. Combined with the calibration above: the
_nominal_ estimated-token ceiling (12,000) is unmeetable-with-headroom at
any grid point, but the _real_ (cl100k-proxy) worst case is ~9.5k–10k
tokens — comfortably inside a real client's budget even though the
estimator's own headroom bar can't be satisfied. The knee decision below is
therefore made on gold-recall + edge delivery, not on the headroom bar,
which the calibration shows cannot gate this decision.

## 3. #37 monotonicity: mean gold-recall@budget by `edge_budget`

`mcp/tests/gold/compute_monotonicity.py` scores the final fixed-11 sweep
JSON against the frozen cross-family labels (`labels.json`) at `top_k=5,
dedup=on`, generalizing `compare_to_baseline.py`'s single-row scoring
across the full `edge_budget` grid (budget-independent
`gold_recall_at_budget` denominator — the tuned parameter never appears in
the metric that judges the tuning, critic finding F1):

```
$ python3 mcp/tests/gold/compute_monotonicity.py mcp/tests/battery_results/2026-07-20-pr6-final-sweep.json
```

| edge_budget | mean_gold_recall | delta vs prior | verdict  |
| ----------- | ---------------- | -------------- | -------- |
| 20          | 0.186            | -              | -        |
| 40          | 0.268            | +0.082         | OK       |
| 60          | 0.281            | +0.013         | OK       |
| 80          | 0.248            | -0.033         | DECREASE |
| 120         | 0.225            | -0.023         | DECREASE |

**#37's criterion ("raising `edge_budget` never decreases mean gold-recall")
is recorded PARTIALLY MET**, per maintainer decision: monotone
non-decreasing through `edge_budget=60` (tolerance 0.005 absolute), then
decreasing past it (-0.033 at 80, -0.023 at 120 relative to the prior
point). This pattern is **consistent with a ceiling-mediated effect rather
than a selection regression**: relevance-aware selection (PR 3) makes a
larger budget genuinely more relevant up to ~60, and the falloff coincides
with `edge_budget` values where the response-ceiling trim is known (§2) to
be engaging on essentially every query. The committed sweep data pins the
_correlation_ — recall rises then falls in step with the budget crossing
the ceiling-saturation point — but doesn't itself localize the mechanism
down to "the trim specifically removes gold-relevant edges from a larger
candidate set" vs. some other interaction between allocation size and
selection; that finer attribution isn't claimed here. #37 was already
closed by the maintainer (docs half, PR #49); this evidence completes its
outstanding monotonicity record for critic gate 3, recorded here as
partially-met with the correlation above rather than re-opened as failed.

## 4. Default-change decision

**Ship `edge_budget=60`** (was 40) as the `enrich` default. Rationale: 60 is
the best measured point on the mean gold-recall curve (0.281, +0.013 over
the previous default's 0.268) and the smallest `edge_budget` at that peak —
raising it further only adds latency and, per the finding above, actively
reduces relevance. `top_k` stays 5 (unchanged; a RAG-breadth lever the
KG-only gold-recall metric doesn't score — see each sweep's "Knee" section
for the `top_k=3` caller-guidance note, unaffected by this decision).

The identifier-cohort sweep's own generated "Knee" section suggests a
_different_ value, `edge_budget=120` — its retention curve is still rising
at the top of the grid (0.050→0.425 across 20→120, never plateauing; see
`2026-07-20-pr6-final-identifier-sweep.md`'s Knee section). This is not a
contradiction: the identifier cohort has only 2 of its 4 queries with any
KG pool at all (`id_disc_plugin`/`id_mim_plugin` pool zero KG edges — a
known, separately-tracked extraction deficiency), so its curve is
noisy and low-n by construction and was never the acceptance metric.
**The default-change decision is driven by the fixed-11 cohort's mean
gold-recall@budget alone** (§3's table) — the identifier cohort's own
knee is informational, not gating, consistent with how #37's
monotonicity criterion and every prior PR's gold-recall floor have always
been scored on the fixed-11 cohort.

Applied in: `mcp/enrich.py` (`enrich()` signature default), `mcp/server.py`
(the `enrich` tool's `edge_budget` `Field` default + description +
docstring), `mcp/RESPONSE_SHAPES.md` (envelope table row, size-guards
paragraph, new v0.9.0 behavioral-change callout, pin bump to v0.9.0),
`mcp/tests/battery_sweep.py` (`SHIPPED = (60, 5)` — the sweep's own
"currently-committed defaults" comparator), `CHANGELOG.md` (`v0.9.0`
entry). `mcp/tests/gold/compare_to_baseline.py`'s `SHIPPED = {"edge_budget":
40, ...}` is left at 40 with a one-line comment: it selects the row in
PR 3's _historical_ baseline-comparison sweep, not the live default.
`mcp/tests/battery_queries.py`'s `IDENTIFIER_QUERIES` per-query
`edge_budget=40` values are frozen cohort parameters (pinned by
`test_identifier_queries_match_contract`) — untouched, unrelated to the
server default.

Release label: `release:minor` (behavioral default change, per the Global
Constraints' decide-at-evidence-time rule).

## 5. Template-fix note (M1/M2/M3, carried from the PR 1 final review)

Landed in Task 1 (commit `d1ba1c8`), confirmed present in this run's
generated sweep `.md` files:

- **M1** — `battery_sweep._render`'s knee-differs text used to claim a
  default change was _applied_; it now recommends one
  (`sweep_io.render_knee_verdict`) — see both final sweeps' "Chosen
  defaults" / "Differs from the shipped" lines, which correctly state
  "defaults unchanged by this run — apply via a config/docs commit ... if
  accepted" (accepted by this task).
- **M2** — the generated report title now uses `SWEEP_TAG`
  (`sweep_io.render_title`) instead of a hardcoded `2026-07-17`; both final
  sweeps here are titled `2026-07-20-pr6-final` / `2026-07-20-pr6-final-identifier`.
- **M3** — `_install_counters` now wraps `embed_batch` (in addition to
  `embed`), so relevance-scoring's per-request batch-embed call is no
  longer silently uncounted.

## 5a. Final-review fix wave (this commit)

The whole-branch final review found two more issues in the measurement
tooling itself and several evidence-wording minors, all fixed here:

- **B1 (evidence staleness)** — commit `78938ca` added 2 new
  `*worst-response.json` dumps (this PR's own final runs) to the
  calibration corpus, so `token_calibration.json` (15 rows at that commit)
  no longer reflected HEAD's actual 17-file corpus. Re-ran
  `calibrate_tokens.py`; the two new rows (est 12013/real 9513/ratio 1.263,
  est 12011/real 10053/ratio 1.195) don't change the conclusion — still
  `e=0.0`, bar 9000. §1 above and the CHANGELOG now say 17 dumps, not 15.
  Also added the missing `main()` docstring to `calibrate_tokens.py`
  (carried minor (a) from the PR 1 final review).
- **B2 (falsified template prose)** — `battery_sweep._render`'s "Finding 2"
  paragraph hardcoded a specific PR 1 (#28) claim (retention still peaks
  at `edge_budget=40`, "~28 mean" edges, "predicate recall stays ~1.0")
  that this very run's own data falsifies: the fixed-11 cohort's retention
  actually peaks at `edge_budget=80` (0.918), not 40, and the identifier
  cohort's numbers (mean edges delivered ~10–11.5, predicate recall flat
  at 0.500) don't match the hardcoded ones at all. Fixed the template:
  extracted a pure, numpy-free `sweep_io.render_finding_2` helper (takes
  the per-`edge_budget` curve, `top_k`, and the `edge_budget` list) that
  reports the run's actual measured peak/range instead of asserting a
  fixed historical one; `_render` now calls it.
  Unit-tested in `test_sweep_io.py` (3 new tests, including one using this
  PR's real curve values, pinning that the old hardcoded strings never
  appear and that a peak at 80 — not 40 — is reported correctly). The two
  committed `2026-07-20-pr6-final{,-identifier}-sweep.md` reports were
  erratum-edited in place with the corrected paragraph (computed by
  actually invoking `sweep_io.render_finding_2` against their real
  committed JSON, not hand-transcribed) plus a one-line note that
  regenerating the report from scratch would need the live battery stack.
  Follow-up filed for the other 16 pre-existing `battery_results/*.md`
  files carrying the same falsified prose (regeneration needs the live
  stack): [#57](https://github.com/whiffernet/embeddington/issues/57).
- **M-1** — widened the worst-case token-range claim in §2 and the
  CHANGELOG to state the true full-grid range (~10,300–12,013) rather than
  only the `edge_budget≥40` sub-range (≥11,900) it was narrowed to before.
- **M-2** — softened the monotonicity §3 / CHANGELOG "root cause" language
  from an assertion to "consistent with a ceiling-mediated effect" — the
  committed sweep data pins the correlation (recall falls where the
  ceiling trim is known to engage) but doesn't itself localize a causal
  mechanism.
- **M-3** — "#37 was already closed by Erik" → "closed by the maintainer"
  (this doc doesn't otherwise name Erik, and the identity isn't load-bearing
  to the claim).
- **M-4** — added a paragraph to §4 acknowledging the identifier-cohort
  sweep's own knee suggests `edge_budget=120` (its retention curve never
  plateaus in-grid) and explaining why the shipped decision uses the
  fixed-11 cohort's gold-recall curve instead (consistent with every prior
  PR's gating metric).
- **M-5** — fixed a latent bug in `compute_monotonicity.py`: `met_through`
  could resurrect past an interior DECREASE if a later point happened to be
  non-decreasing relative to _its own_ prior point (e.g. OK, DECREASE, OK
  would have reported "non-decreasing through" the second OK). Now only
  advances while the curve hasn't broken yet. Doesn't change this run's
  printed table (its curve breaks once, at 80, and never recovers), but
  the fix is real and would matter on a different-shaped curve.
- **Carried (b)** — added the same historical-row-selector comment to
  `compute_gold_baseline.py`'s `SHIPPED` constant that `compare_to_baseline.py`
  already carries (Task 3's original commit fixed the latter, missed the
  former).

## 6. Scope

Selection, threshold, lexical-lane, and grounding-classification logic are
**untouched** — this PR changes one default value plus its documentation
and the measurement tooling. The full diff is: `enrich.py`'s signature
default, `server.py`'s `Field` default/description/docstring, docs
(`RESPONSE_SHAPES.md`, `CHANGELOG.md`, tests), the sweep-template fixes
(Task 1, already committed), the calibration script (Task 2, already
committed), this evidence + the `compute_monotonicity.py` script, and the
§5a final-review fix wave (`calibrate_tokens.py` docstring + re-run,
`sweep_io.render_finding_2` + its unit tests, `compute_monotonicity.py`'s
`met_through` guard, `compute_gold_baseline.py`'s `SHIPPED` comment, the
two erratum-edited sweep `.md` reports, and this doc's wording fixes).
