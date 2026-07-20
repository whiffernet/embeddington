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

| group (ratio = est/real)                                             | files | est   | real      |
| -------------------------------------------------------------------- | ----- | ----- | --------- |
| 1.263 (baseline-pre36, pr3-final, pr4-final, pr3-arm ×5, pr4-thr ×3) | 11    | 12000 | 9501–9503 |
| 1.195 (pr4-final-identifier, pr4-thr ×3-identifier)                  | 4     | 11998 | 10041     |

**Finding: `estimate_tokens` overestimates on all 15 dumps (ratio 1.195–1.263,
never < 1.0) — it never underestimates.** So `e = max(0, max(1 − ratio)) =
0.0`, and the calibrated bar `int(9000 × (1 − e)) = 9000` — unchanged from
the nominal bar. Real worst-case responses run ~9.5k–10k real (cl100k)
tokens against a 12,000-estimated-token ceiling; the heuristic's own
`TOKEN_DIVISOR = 3` comment ("deliberately pessimistic") is confirmed
correct in direction — most BPE tokenizers land closer to ~4 chars/token on
JSON-like text than 3.

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
every combo's worst-case query sits at ~11,900–12,013 (essentially the
12,000 ceiling), regardless of `edge_budget` or `top_k`. Real RAG chunks
(~2k tokens each) plus rich KG edges (~260 tokens each) saturate the
ceiling; the response-ceiling trim fills to just under it no matter what
the caller asks for, so the ≥25%-headroom bar (≤9000 est-tokens) cannot
discriminate between grid points — it isn't wired to `edge_budget`/`top_k`
at all.

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
point). This is a **ceiling-mediated effect, not a selection regression**:
relevance-aware selection (PR 3) makes a larger budget genuinely more
relevant up to ~60 by letting the diversity-quota/relevance-ranked
selector draw from a bigger pool before the ceiling trim engages; past ~60
the extra allocation increasingly competes with itself and with the vector
half for the same fixed token space, so the trim removes lower-value edges
from a larger candidate set — net relevance falls rather than plateauing.
#37 was already closed by Erik (docs half, PR #49); this evidence completes
its outstanding monotonicity record for critic gate 3, recorded here as
partially-met with the mechanism above rather than re-opened as failed.

## 4. Default-change decision

**Ship `edge_budget=60`** (was 40) as the `enrich` default. Rationale: 60 is
the best measured point on the mean gold-recall curve (0.281, +0.013 over
the previous default's 0.268) and the smallest `edge_budget` at that peak —
raising it further only adds latency and, per the finding above, actively
reduces relevance. `top_k` stays 5 (unchanged; a RAG-breadth lever the
KG-only gold-recall metric doesn't score — see each sweep's "Knee" section
for the `top_k=3` caller-guidance note, unaffected by this decision).

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

## 6. Scope

Selection, threshold, lexical-lane, and grounding-classification logic are
**untouched** — this PR changes one default value plus its documentation
and the measurement tooling. The full diff is: `enrich.py`'s signature
default, `server.py`'s `Field` default/description/docstring, docs
(`RESPONSE_SHAPES.md`, `CHANGELOG.md`, tests), the sweep-template fixes
(Task 1, already committed), the calibration script (Task 2, already
committed), and this evidence + the new `compute_monotonicity.py` script.
