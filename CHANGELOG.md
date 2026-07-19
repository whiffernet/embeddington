# Changelog

## v0.7.0 — 2026-07-19

Closes #38 (hybrid vector retrieval: a lexical MatchText lane for
identifier-style tokens, fused with the dense lane by reciprocal-rank
fusion, plus a minimum-score floor on the dense lane).

- Vector retrieval (`vector_search`'s `results`, `enrich`'s `vector_chunks`)
  is now hybrid: the dense (cosine) lane is merged via reciprocal-rank
  fusion with a lexical lane per identifier-like token found in the query
  (snake_case or dotted, e.g. `cmdb_rel_ci`, `com.snc.discovery`, capped at
  3 tokens). Qdrant's word tokenizer splits identifiers on
  underscores/punctuation, so the lexical lane post-filters to chunks
  containing the **literal** token (case-insensitive) before fusion.
  Measured: the identifier query cohort gets a literal-match chunk in its
  fused top-5 **4/4** live. See `mcp/tests/gold/PR4-EVIDENCE.md`.
- New `EMBEDDINGTON_SCORE_THRESHOLD` env knob (default `0.50`, measured —
  legitimate battery queries bottom out around 0.56, nonsense probes top
  out around 0.45): the dense lane drops chunks scoring below it instead of
  padding weak matches in to hit `top_k`/`limit`. BEHAVIORAL:
  `vector_search`/`enrich` can now legitimately return **fewer** results
  than requested. Measured: a nonsense-query probe went from 5 padded
  chunks at threshold `0.0` to 0 at the shipped `0.50`.
- Lexical lane depth is `max(top_k*2, 25)`, not just `top_k*2` — measured:
  common-subtoken identifiers (`pm_project` → `{pm, project}`) push a
  literal-token chunk as deep as rank 14–23 in the subtoken-filtered lane,
  so a shallower fetch was post-filtering to empty for them.
- New consumer-local `chunk_text` full-text index (never part of the
  published baseline/diff snapshots): the server ensures it at every start
  and lazily re-checks (at most once per 60s per process) when not yet
  `"ready"`, self-healing after a baseline restore recreates the Qdrant
  collection and drops it. New standalone `ensure-index` command
  (`embeddington-consume ensure-index`), and a baseline import now warms it
  automatically as part of the restore (measured: 152,191/152,194 points
  materialized in ~3m30s on the reference stack).
- BEHAVIORAL: when the `chunk_text` index isn't `"ready"` (`"building"`,
  `"absent"`, or `"unavailable"`) and the query itself contains identifier
  tokens, the lexical lane is skipped and `warnings` gets the exact string
  `"lexical lane degraded — chunk_text index not ready"` — never a silent
  drop when the caller might have expected a literal-token hit.
- Fixed-11 gold-recall, paired against v0.6.0: 10/11 non-worse (mean
  0.283→0.268). The one dip, `control_no_hints_snake`, is explained in
  `PR4-EVIDENCE.md`: the query's own identifier token changed its fused
  chunk mix, which shifted which KG edges the response-ceiling trim kept —
  a KG-side dip that buys a vector-side gain the KG metric doesn't see.
- `hub_discovery`/`hub_cmdb` (deferred from `v0.6.0` on the thesis that the
  lexical lane would fix them) are recorded **FAILED, not re-deferred**:
  their phrasing carries no identifier tokens, so PR 4's mechanism
  structurally cannot reach them. Maintainer-accepted measured limit;
  follow-up filed as
  [#52](https://github.com/whiffernet/embeddington/issues/52) (hub-entity
  queries need a relevance signal neither dense cosine nor lexical matching
  provides).

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
