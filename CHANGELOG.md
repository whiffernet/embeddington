# Changelog

## v0.6.0 — 2026-07-19

Closes #36 (relevance-aware edge selection: two-phase diversity quota +
cosine relevance replaces confidence-only selection in `enrich`'s KG half).

- `match.edges[]` selection is now two-phase: a diversity quota (default
  `0.40` of a concept's slots, `EMBEDDINGTON_DIVERSITY_QUOTA`) picks the
  best edge per distinct predicate ranked by query relevance, then the
  remaining slots fill by relevance. Relevance is bge-m3 cosine similarity
  between each edge's `source_quote` and the query. Measured lift: mean
  gold-recall 0.130 -> 0.283 (2.2x) on the frozen gold set
  (`mcp/tests/gold/`), reviving four formerly-zero queries (`c1`,
  `hub_incident`, `control_no_hints_snake`, `hub_cmdb_rel_ci`); mean
  per-predicate recall 0.977. See `mcp/tests/gold/PR3-EVIDENCE.md`.
- New `EMBEDDINGTON_DIVERSITY_QUOTA` env knob (default `0.40`) — server
  config, not a tool parameter, same posture as
  `EMBEDDINGTON_MAX_RESPONSE_TOKENS`.
- Quote embeddings are fetched with one batched `embed_batch()` call per
  `enrich` (order-preserving, validated), served from a bounded in-process
  LRU cache. Honest cost: cold (CPU embed sidecar, no cache hits) 188ms ->
  ~12.2s at the shipped combo (~45ms/quote, up to ~300 pool quotes
  batch-embedded); warm (cache hit, or a GPU-backed embed service) back
  down to ~200ms, at or below the pre-change baseline.
- BEHAVIORAL: any embed failure degrades loudly to the legacy predicate-floor + confidence-fill order (byte-identical to `v0.5.1`) and adds the warning `"relevance scoring unavailable — selection degraded to confidence order"` to `warnings` — never a silent fallback.

## v0.5.0 — 2026-07-18

Closes #46 (measurement foundation: `updated_at` envelope surfacing, battery
instrumentation, frozen gold set, pre-change baseline).

- `updated_at` surfaced on KG nodes and edges in `enrich`/`kg_neighbors`/`kg_find_entities`
  (recency visible, not ranked; see RESPONSE_SHAPES.md).
- Battery instrumentation: JSON results, sub-call counts, repeated-run latency
  (median/IQR); frozen cross-model-validated gold set (`mcp/tests/gold/`, 121
  relevant of 2,765 pool edges, cross-family 2-of-3 majority) + committed
  pre-change baseline (mean gold-recall 0.130) with the PR 3 acceptance floor
  (≥0.280) pinned.

## v0.3.0 — 2026-07-17

Fixes #28 (enrich payloads exceeded the MCP client tool-result cap).

- `enrich`: response-level `edge_budget` (default 40 total — the sweep knee;
  see `mcp/tests/battery_results/2026-07-17-sweep.md`) with
  relevance-weighted allocation, concept dedup (same-name entity variants
  expand once, all facets merged), predicate-diversity selection,
  explicit `truncation`/`suggest`, per-concept error scoping, `predicates`
  filter (validated, case-insensitive), snake_case regex fallback.
- Server-side response ceiling `EMBEDDINGTON_MAX_RESPONSE_TOKENS`
  (default 12000 est-tokens) trims the WHOLE response deterministically
  with per-concept floors. The trim prunes orphan nodes: dropping an edge
  also removes any node it leaves without a surviving edge, so `match.nodes`
  always equals the endpoints of `match.edges` and the freed node-tokens are
  reused for edges — fixing an edge-count inversion where a larger
  `edge_budget` overshot the ceiling and returned FEWER edges.
- `kg_neighbors`: explicit `truncation` object.
- `kg_find_entities`: entities now carry `degree`.
- BEHAVIORAL: `top_k` default 10→5; default edge volume ~10× smaller.
  See RESPONSE_SHAPES.md callout.
