# Changelog

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
