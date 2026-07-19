# Gold-set labeling protocol (spec §3.3)

Labels are produced by adversarially-prompted Claude subagents, frozen here,
and validated against a human-labeled subset (JUDGE-VALIDATION.md) before any
gate trusts them. Rebuild trigger: any change to pools.json fingerprints.

## Judge prompt (verbatim, one subagent per query)

> You are labeling knowledge-graph edges for retrieval-relevance ground truth.
> QUERY: <query text>
> For EACH candidate edge below (id, predicate, source_quote, source_document),
> assign exactly one label:
>
> - relevant: the edge's fact is directly useful in answering the query.
> - marginal: topically related but would not change the answer.
> - irrelevant: unrelated to what the query asks.
>   Judge ONLY the edge content against the query. Do NOT consider edge
>   confidence, extraction_type, or how an embedding model might score it.
>   Be stingy with "relevant" — when torn between relevant and marginal, choose
>   marginal. Return JSON: {"<edge_id>": {"label": "...", "rationale": "<one
>   sentence citing the quote>"}} for every edge id given, no omissions.

## Skeptic pass (verbatim, one subagent per query, sees only "relevant" verdicts)

> You are auditing relevance labels another judge produced. QUERY: <query text>
> For each edge labeled "relevant" below, argue it DOWN: is the edge's fact
> actually load-bearing for answering this query, or merely on-topic?
> Demote to "marginal" unless the rationale survives your attack. Return the
> same JSON shape with your final label + one-line rationale for each.

Demotions by the skeptic are final. Labels for "marginal"/"irrelevant" from
the first pass are not re-examined (asymmetric by design — the metric only
counts "relevant", so false positives are the dangerous error).

## Freezing

labels.json must cover every edge id in pools.json exactly (no extras, no
omissions) — enforced by tests/test_gold_artifacts.py. Scope-scrub
(scope_scrub.sh) must pass before commit.

## Revision — cross-family majority construction (2026-07-18)

The human validation gate was replaced by a cross-model referee (maintainer decision),
which REJECTED the judge+skeptic labels (precision 0.41 vs gpt-oss-120b; llama-3.3-70b
tiebreak sided with the referee 14/15). Final gold-relevant is therefore a **2-of-3
family majority vote** (Claude judge / gpt-oss-120b / llama-3.3-70b, identical rubric,
temperature 0, blind): 121 relevant of 2,765. Full history, per-round numbers, and the
bar disposition live in JUDGE-VALIDATION.md. Rebuilds must reproduce the whole pipeline:
judge -> skeptic -> both referee-family votes -> majority.

## Identifier cohort (2026-07-19)

**Built for:** PR 4 / issue #38

**Queries:** Four natural-language identifier-lookup queries with controller-verified literal corpus presence:

1. "What does the com.snc.discovery plugin activate?" → `id_disc_plugin` (pools ZERO edges)
2. "What does the com.snc.incident.mim plugin provide for major incident management?" → `id_mim_plugin` (pools ZERO edges)
3. "What is the pm_project table used for?" → `id_pm_project` (pools 152 edges, 7 relevant)
4. "What is the sc_cat_item table used for?" → `id_sc_cat_item` (pools 212 edges, 6 relevant)

**KG extraction deficiency:** The two dotted-plugin queries (`id_disc_plugin`, `id_mim_plugin`) yield zero edges
from the KG because the entity-hint extractor (`_extract_entity_hints`) cannot resolve dotted identifiers in
NL phrases. This is a **measured, documented deficiency** in the extraction ontology — vector-lane gates on
the MCP still cover all four queries despite the gap.

**Labeling:** Identical judge→skeptic→2-referee majority pipeline as the main fixed cohort. Result: 13 candidate
edges (both non-empty queries pooled) with **unanimous 3/3 cross-family majority** labels (Claude, gpt-oss-120b,
llama-3.3-70b). All 7 id_pm_project relevants + 6 id_sc_cat_item relevants survived skeptic review.
