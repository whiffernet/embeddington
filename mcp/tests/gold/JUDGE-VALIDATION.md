# Judge validation (spec §3.3) — method history and final construction

## Final construction: cross-family 2-of-3 majority (ACCEPTED 2026-07-18)

An edge is gold-**relevant** iff at least 2 of 3 independent model families judged it
relevant under the identical PROTOCOL.md rubric, temperature 0, blind to each other:

| voter     | model                                      | route                         |
| --------- | ------------------------------------------ | ----------------------------- |
| judge     | Claude (sonnet) + adversarial skeptic pass | this repo's labeling workflow |
| referee 1 | openai/gpt-oss-120b                        | Groq API                      |
| referee 2 | llama-3.3-70b-versatile                    | Groq API                      |

Result: **121 relevant of 2,765 pool edges** (from 258 judge-pass candidates; 137
majority-demoted to marginal, 18 gpt-oss vetoes overturned by claude+llama majority).
Every affected edge's rationale records its per-family votes. Per-query relevant counts:
c1=10, c2=13, cmdb_rel_ci=14, procmin=11, disc=14, cmdb=14, incid=7, predint=17,
ctl_nh=6, ctl_pf=14, ctl_ml=1.

## History — how the gate got here (nothing hidden)

1. **Planned human gate replaced.** The spec's original gate was a human-labeled 30-edge
   sample (precision ≥ 0.80). The maintainer judged hand-labeling impractical (the KG
   exists precisely because this knowledge isn't held in one head) and chose a
   cross-model referee instead (decision 2026-07-18).
2. **Round 1 — REJECTED.** gpt-oss-120b refereed the seed-46 sample: judge precision on
   `relevant` **0.41**, recall 0.90. Not noise: llama-3.3-70b tie-broke the 15
   disagreements and sided with the referee **14–1**. Diagnosis: the Claude judge+skeptic
   pipeline over-labeled `relevant` at the relevant/marginal boundary (~2×), heaviest on
   the multi-part `case1` query (facts touching any sub-ask were credited as relevant).
3. **Revision.** Per the protocol's rejection path, all 258 judge-relevant candidates
   were re-voted by both referee families; final labels are the 2-of-3 majority above.
4. **Round 2 measurement (intermediate 103-edge intersection set, seed-47 sample, llama
   referee): precision 0.73, recall 1.00.** Every dissent was relevant-vs-marginal; none
   was relevant-vs-irrelevant.
5. **Bar disposition.** The literal "precision ≥ 0.80 vs a single outside referee" bar is
   **replaced, not met** — recorded deliberately (maintainer decision 2026-07-18):
   single-model dissent on the relevant/marginal boundary proved irreducible (~25–30%),
   comparable to human inter-annotator disagreement on graded relevance. The majority
   construction is strictly stronger per edge than any single referee's verdict: every
   gold-relevant edge carries ≥2 independent family votes.

## Independence (circularity check)

Judge–cosine AUC over the final labels: **mean 0.614** (per query in
`independence.json`, range 0.34–0.89). bge-m3 cosine only weakly predicts the gold
labels, so a selector that ranks by cosine cannot mechanically saturate gold-recall —
the property the metric exists to guarantee. `hub_cmdb_rel_ci` (0.34) shows cosine
_anti_-correlating with relevance on an identifier-style query.

## Rebuild triggers

Rebuild labels (PROTOCOL.md pipeline + this majority construction) whenever pools.json
fingerprints change. `compute_agreement.py` remains the single-referee measurement tool
used in rounds 1–2; `VALIDATION_SAMPLE.md` holds the seed-47 referee sample.
