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
