# Retrieval-quality chain — closing audit (2026-07-20)

This is the final record of the 2026-07 retrieval-quality chain: six issues
(#46, #36, #37, #38, #47, #44), six merged PRs (#48, #49, #50, #53, #54, #56),
releases v0.5.0 through v0.9.0.

Before closing the milestone, each issue was put through an independent
adversarial audit: one auditor per issue (Claude Sonnet 5), each handed the
issue's literal acceptance criteria and the committed evidence, and instructed
to **prove the issue was NOT genuinely closed** — criteria gamed, reworded,
measured on the wrong thing, or evidence that does not reproduce. Auditors
re-ran the committed evaluator scripts (`compute_gold_baseline.py`,
`compare_to_baseline.py`, `compute_monotonicity.py`, `calibrate_tokens.py`,
`compute_independence.py`), recomputed metrics directly from the committed
sweep JSONs and gold labels, cross-checked git history against the narrative
record, ran the test suites, and (where a live battery stack was available)
reproduced the live-verified gates. One auditor mutation-tested the post-trim
grounding ordering by temporarily inverting it and confirming the regression
test fails.

## Verdicts

| Issue                                                              | Closed by                                   | Audit verdict       |
| ------------------------------------------------------------------ | ------------------------------------------- | ------------------- |
| #46 — measurement first (latency, call counts, baseline, gold set) | PR #48 (v0.5.0)                             | CLOSED_WITH_CAVEATS |
| #36 — edge selection ignores relevance                             | PR #50 (v0.7.0)                             | CLOSED_WITH_CAVEATS |
| #37 — raising `edge_budget` lowers retention                       | PR #49 (v0.6.0), record completed by PR #56 | CLOSED_WITH_CAVEATS |
| #38 — identifier-style queries miss literal matches                | PR #53 (v0.7.1)                             | CLOSED_WITH_CAVEATS |
| #47 — padded results instead of a no-grounding signal              | PR #54 (v0.8.0)                             | CLOSED              |
| #44 — worst-case response blows the token ceiling                  | PR #56 (v0.9.0)                             | CLOSED_WITH_CAVEATS |

No issue was found NOT_CLOSED. Every caveat below was already recorded in the
committed evidence before the audit ran — the auditors confirmed the record is
honest; they did not discover undisclosed gaps. Key numbers the auditors
reproduced independently, exactly: baseline mean gold-recall 0.130
(`BASELINE.md`, byte-for-byte regeneration), shipped gold-recall 0.283 (PR 3)
and 0.268 (PR 4, 10/11 non-worse), the monotonicity curve
0.186/0.268/0.281/0.248/0.225 (verdict "PARTIALLY MET" from the script
itself), calibration e=0 over all 17 dumps (ratios 1.195–1.263), the
judge-independence AUCs (mean 0.614), the 258→121 label-revision arithmetic
against git history, and the Finding-2 template text as genuinely
data-derived. The #47 auditor additionally reproduced both load-bearing live
gates against a running battery stack: tier=`none` on the nonsense probe and
tier=`weak` with the exact recorded reason strings on the incident-class
probe.

## What the chain delivered

- A measurement footing that did not exist before: latency + per-call counts
  in every sweep, a committed pre-change baseline, and a frozen cross-family
  gold set (2,765 pooled edges, 121 relevant by 2-of-3 model-family majority)
  with its full validation history — including the round-1 REJECTION of the
  single-judge construction — preserved in `mcp/tests/gold/JUDGE-VALIDATION.md`.
- Relevance-aware two-phase edge selection (diversity quota 0.40): mean
  gold-recall 0.130 → 0.283, c1 0.000 → 0.200, hub_process_mining
  0.091 → 0.455, hub_incident 0.000 → 0.429.
- A hybrid lexical lane + score threshold (0.50): 4/4 identifier queries hit
  literal chunks in fused top-5 (live-verified, labeled as such); nonsense
  queries return 0 results instead of 5 padded ones.
- A grounding tier (`ok`/`weak`/`none`) classified on post-trim content, with
  a mutation-killing regression test pinning the incident class
  (on-topic-but-nonexistent identifier ⇒ `weak`, fake id never emitted).
- A calibrated token estimator (÷3 heuristic overestimates 19–26%, never
  underestimates) and a re-tuned default `edge_budget=60` sitting at the
  measured gold-recall peak.

## Caveats confirmed by the audit (all pre-recorded)

1. **Hub queries, half-solved.** hub_discovery and hub_cmdb never improved
   (0.214→0.143 regression, 0.071→0.071 flat) and are recorded FAILED as a
   measured limit of cosine+lexical relevance — open follow-up
   [#52](https://github.com/whiffernet/embeddington/issues/52).
2. **Monotonicity is partial.** Gold-recall is non-decreasing only through
   `edge_budget=60`; it falls at 80 and 120 (confirmed on every top_k slice,
   ruling out slice cherry-picking). The shipped default sits at the peak.
   Recorded PARTIALLY MET everywhere; no doc claims a full pass.
3. **#37 was closed early.** The GitHub issue closed at the docs PR (#49) a
   day before its monotonicity evidence existed (PR #56). Every later
   artifact says so plainly; the auditor confirmed nothing papered it over.
4. **Two floor amendments** (per-predicate mean vs worst-query; PR 3 recall
   floor) and the **headroom-bar amendment** (0/15 combos meet the 9000-token
   bar; ceiling-knob matter, not an `edge_budget` knob) are maintainer
   decisions recorded with mechanistic rationale in `gold/README.md` and the
   evidence docs.
5. **Two gates are live-verified only** (4/4 identifier hits, nonsense 5→0)
   because the sweep JSON format stores KG edge ids, not vector chunks. The
   evidence labels them as such; the auditor verified the format limitation
   is real and found one committed worst-response artifact that incidentally
   corroborates an identifier hit.
6. **Grounding is observation-only breadth.** A non-identifier query whose
   retrieval clears the score threshold reads `ok` even if shallowly
   on-topic; `vector_search` (the separate tool) carries no grounding signal.
   Both recorded in `PR5-EVIDENCE.md`.

## New findings from the audit itself (minor; recorded here, no reopen)

- **Call counts are captured but not rendered.** Per-combo call counts exist
  complete in every sweep JSON but no MD table aggregates them; a reader must
  sum the JSON. The CHANGELOG describes this accurately, so it is a disclosed
  shortfall against #46's literal wording, not a hidden one.
- **Round-1 rejection numbers are narrative-backed.** The 0.41/0.90 referee
  precision/recall and 14–1 tie-break are corroborated by git-history label
  arithmetic (258→121) but no raw round-1 referee artifact was preserved the
  way round 2's sample was.
- **PR #56's body says "218 mcp" tests;** the reproducible counts at the
  merge commit are 245 collected / 221 passed + 24 live-gated skips. Prose
  discrepancy only; every load-bearing number in the evidence reproduced.
- **`compute_independence.py` is not bit-deterministic** (<0.001 AUC drift on
  re-run, float/tie-breaking noise). Immaterial to any conclusion.

## Open backlog seeded by the chain

- [#51](https://github.com/whiffernet/embeddington/issues/51) — precompute
  quote embeddings (kills the cold-path relevance latency).
- [#52](https://github.com/whiffernet/embeddington/issues/52) — hub-entity
  queries need a non-cosine relevance signal.
- [#55](https://github.com/whiffernet/embeddington/issues/55) — signal
  fidelity trio.
- [#57](https://github.com/whiffernet/embeddington/issues/57) — 16 legacy
  sweep reports carry the pre-fix Finding-2 template prose.
- Deferred rounds: provenance (#39–#42) and the ontology epic (#43) are
  producer-side and were explicitly out of scope for this chain.

With this record committed, the "Retrieval quality — 2026-07" milestone
closes.
