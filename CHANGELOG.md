# Changelog

## v0.12.2 — 2026-09-02

The fatal startup probes are now bounded, retried only where retrying can help, and say
what actually failed (PR #110). The prompting incident: the MCP refused to start while
Qdrant was still coming up, and the message named neither the host nor the reason.

- BEHAVIORAL: the `/embed` probe is now **fatal**, alongside Qdrant — it was warn-only.
  Both dependencies live on the same host in the common deployment, so a host restart
  takes them down together; giving only one of them a retry would make that retry
  pointless. An unreachable `/embed` now refuses startup rather than exposing an
  `enrich` that cannot work.
- Startup wall-clock is bounded. The per-attempt timeout is 5s
  (`STARTUP_PROBE_TIMEOUT`), no longer inherited from `HTTP_TIMEOUT` (30s, sized for
  real queries), and total retry time per probe is capped at 15s
  (`STARTUP_PROBE_DEADLINE`). Measured against an unresponsive host, the worst case
  fell from ~134s to **12.1s**. That is the number that matters: MCP clients enforce an
  initialization timeout, and exceeding it replaces a clear `Refusing to start` with an
  opaque client-side disconnect carrying no diagnostic at all. The fatal probes run
  concurrently, so the worst case is the deadline plus one probe timeout — not the sum
  across probes.
- Only transport failures are retried. `RETRYABLE_TRANSPORT_ERRORS` is
  `(httpx.TimeoutException, httpx.NetworkError)`; a typo'd scheme, a redirect loop or a
  decoding error fails on the first attempt, because no number of retries fixes them.
- The refusal carries its own diagnosis — `no response from … after 2 attempt(s) in
  12.0s — last error: ConnectTimeout` for an outage, `answered HTTP 401 — the collection
  is missing, or the credential was rejected` for a rejection, and `failed and cannot be
  retried (UnsupportedProtocol: …)` for a bad URL.
- NEW: `mcp/probe.py`, the shared retry policy both fatal probes use.
- The four `EMBEDDINGTON_STARTUP_*` knobs are documented in `mcp/.env.example`. The one
  trade-off: a genuine misconfiguration against an unreachable host now surfaces in ~12s
  instead of instantly. Set `EMBEDDINGTON_STARTUP_RETRIES=0` to restore fail-on-first.

## v0.12.1 — 2026-09-02

Installer: find a container runtime that is already installed, say why a daemon will not
answer, and keep a log of the run. Closes #119 (OrbStack report).

- Runtime sensing runs **before** any install offer. A `docker` that exists but is not on
  this shell's `PATH` read as "no container runtime found", and the user was offered an
  install of the runtime they already had. OrbStack is the case that bites: its CLI lives
  in `~/.orbstack/bin` and reaches `PATH` through a shell-init edit that a non-login shell
  never reads. A binary found — via `EMBEDDINGTON_DOCKER_BIN`, a known location, or typed
  at the new menu option — has its directory prepended to *this process's* `PATH`. The
  user's shell profile is never touched.
- An unreachable daemon now reports the endpoint it dialed, the active context, the other
  configured contexts, and docker's own message — shown **before** the wait rather than
  after it times out. A Docker-Desktop-to-OrbStack migration leaves a `desktop-linux`
  context dialing a socket nothing owns, and telling that user to start their
  already-running daemon burned the full 120s for nothing. `docker info` is bounded at
  20s, so a cold-starting daemon reads as a timeout rather than as a frozen installer.
- NEW: a run journal at `<state dir>/run.log`, wrapping `runner.run` — the installer's
  only subprocess chokepoint. The nightly update job runs through the same entry point,
  so it previously failed silently by construction. Trimmed to its trailing 1 MB on open,
  and it degrades to a no-op rather than failing an install when the state dir is
  unwritable. argv is recorded verbatim, which is only acceptable because no installer
  subprocess carries a secret on its command line; a test pins that so it cannot regress.
- EMB-20/21/22/23 keep their exact meanings. No new error code was needed: once a sensed
  binary is adopted rather than dead-ended, the "installed but unreachable" terminal
  state no longer exists.

## v0.12.0 — 2026-09-02

Read-path Track 1 (KG v3 spec §6; whiffernet/llamaindex #117), re-synced from upstream
claudegraph. `sync_mcp.sh` is now the only way `mcp/` changes.

- BEHAVIORAL: `kg_path` may now return `{nodes: [], edges: [], abstained: true, reason, hubs}`
  — release-mediated candidates are suppressed and hub-mediated ones (an intermediate
  vertex above degree 1000) are refused instead of narrated. `no_path` unchanged.
- `kg_find_entities` seeds from the `entities_v2_search` view when it exists (prod:
  522 ms full scan → tens of ms); consumer installs without the view keep the scan.
- `kg_neighbors` tie-breaks within a confidence band by provenance count then
  per-predicate rank before the row cap.
- `enrich` chunks its `/embed` call at 100 quotes; large pools no longer degrade to
  confidence order.
- Measurement apparatus: `fetch_paths()` records `abstained` as its own outcome.
- Consumer-facing `mcp/requirements.txt` floors `python-arango>=8.3.3`.

## v0.11.26 — 2026-09-02

Schema **3.0.0**. Baseline `baseline-2026-09` re-roots the diff chain.

> _"Strikes and gutters, ups and downs."_ Two breaking changes here — read them before you update.

- **BREAKING: a baseline restore now REPLACES the local collection.** It drops
  and recreates before applying. Previously `create_collection` was a no-op when
  the collection already existed and the restore path was create+upsert only, so
  a baseline applied to a populated install landed _beside_ what was already
  stored — two generations of every document. A fresh-scratch restore could
  never surface this, because the no-op branch only runs when the collection
  exists.
- **BREAKING: `SUPPORTED_SCHEMA_MAJOR` 2 → 3.** An install still on schema 2
  stops with `SchemaVersionError` rather than applying a 3.0.0 baseline under
  merge semantics. Fix is `embeddington-setup --yes`. The gate is
  `major > supported`, so older manifests still apply.
- **The diff chain was re-rooted.** `baseline-2026-09` is the new root with zero
  diffs. The eight diffs that chained onto the previous baselines were dropped,
  not compacted — they described the corpus this baseline replaces, and applying
  them on top would have layered old deltas onto new content.
- **Corpus counts** (`baseline-2026-07c` → `baseline-2026-09`):

  |          |     07c |      09 |
  | -------- | ------: | ------: |
  | vectors  | 171,733 |  70,102 |
  | entities | 338,804 | 355,523 |
  | edges    | 759,923 | 809,806 |

  The vector count falls because the previous corpus carried multiple
  generations of the same document — 37 of 40 sampled documents held more than
  one version of their content (producer-side issue). Fewer vectors, same
  documents: the corpus lost the duplicates, not the content. The 2026-09 corpus uses
  deterministic chunk ids plus a post-ingest prune, so each document is present
  exactly once. Measured on the frozen query set, stale-citation rate went
  0.7963 → 0.0000 and duplicate generations within a `top_k` went 35 → 0.

- **NEW: `EXTRA_QDRANT_COLLECTIONS`** (`mcp/config.py`), empty by default so
  consumers are unaffected. Adds `collection:embed_index` pairs to the allowlist
  for deployments holding the corpus under a different name. The two halves are
  different things — the right is the `/embed` index token that selects the
  encoder — and a mismatch raises nothing, since both encoders are 1024-dim.
  A score collapse is the only symptom.

## v0.9.0 — 2026-07-20

Closes #44 (response-ceiling gate re-tune) and delivers issue #37's
outstanding monotonicity criterion.

- BEHAVIORAL: `enrich`'s `edge_budget` default is now **60** (was 40). Final
  re-tune sweeps (`mcp/tests/battery_results/2026-07-20-pr6-final-sweep.{md,json}`
  and the identifier-cohort counterpart, `SWEEP_REPS=5`, both cohorts,
  v0.8.0 grounding-era envelope) measured mean gold-recall@budget (frozen
  cross-family labels, `top_k=5`, `dedup=on`) by `edge_budget`: 20→0.186,
  40→0.268, **60→0.281**, 80→0.248, 120→0.225. 60 is the best measured
  point (+0.013 over the previous default of 40) and the smallest
  `edge_budget` at the peak.
- Issue #37 (`raising edge_budget never decreases mean gold-recall`) is
  recorded **PARTIALLY MET**: monotone non-decreasing through
  `edge_budget=60`, then decreasing past it. This is consistent with a
  ceiling-mediated effect rather than a selection regression — the falloff
  coincides with `edge_budget` values where the response-ceiling trim is
  known to engage on essentially every query — but the committed data pins
  the correlation, not a proven mechanism. See
  `mcp/tests/gold/PR6-EVIDENCE.md` for the full monotonicity table.
- Calibration finding (informs the headroom bar, does not change shipped
  behavior): `budget.estimate_tokens`'s ÷3 heuristic was calibrated against
  a real tokenizer (tiktoken `cl100k_base` proxy) over every committed
  worst-case response dump. Result: the heuristic **overestimates** tokens
  by 19–26% on all 17 dumps (never underestimates) — `e = 0`, calibrated
  bar unchanged at 9000 estimated tokens. Separately, 0/15 `dedup=on` grid
  combos land at or under that 9000-token headroom bar (across the full
  grid, both cohorts, worst-case tokens range ~10,300–12,013; restricted
  to `edge_budget ≥ 40` it tightens to ≥11,900, essentially the 12,000
  ceiling) — the response-ceiling trim fills to just under the ceiling at
  `edge_budget ≥ 40` regardless of `top_k`, so the ≥25%-headroom bar is
  unmeetable there and cannot discriminate between grid points. This
  restates PR 1's Finding-1 with calibrated numbers (maintainer-authorized
  amendment, not a new deviation): headroom is a ceiling/chunk-size lever
  (`EMBEDDINGTON_MAX_RESPONSE_TOKENS`, `source_quote`/text length), not an
  `edge_budget` one — real payloads (~9.5–10k real tokens per the
  calibration) sit comfortably under the nominal ceiling even though the
  estimated-token headroom bar can't be met.
- Sweep-template honesty fixes (carried from the PR 1 final review):
  `battery_sweep.py`'s knee narration now recommends a default change
  rather than claiming one was applied when the shipped defaults are
  unchanged by a run (M1); the generated report title uses the run's
  actual `SWEEP_TAG` instead of a hardcoded date (M2); `CALL_COUNTS` now
  wraps `embed_batch` as well as `embed` (M3).
- Docs: `enrich`'s tool docstring, `mcp/RESPONSE_SHAPES.md`, and
  `mcp/server.py`'s `edge_budget` parameter description are rewritten with
  the measured v0.9.0 behavior (productive up to ~60, reduces relevance
  past it under the ceiling) in place of the pre-relevance-selection
  0.282→0.200 dilution phrasing from PR 2, which described a different,
  now-superseded selector.
- Committed alongside: the final fixed-11 sweep
  (`2026-07-20-pr6-final-sweep.{md,json}`), the final identifier-cohort
  sweep (`2026-07-20-pr6-final-identifier-sweep.{md,json}`), and both
  sweeps' worst-response calibration dumps.

## v0.8.0 — 2026-07-20

Closes #47 (empty/weak-retrieval guard: an explicit `grounding` signal so a
confident-looking `enrich` response can be told apart from one that didn't
actually find what was asked).

- BEHAVIORAL: `enrich`'s envelope gains a new `grounding` key — `tier`
  (`"ok"` / `"weak"` / `"none"`) plus `reasons` — classified from the FINAL
  (post-ceiling-trim) response content by the pure `mcp/grounding.py`
  classifier, not the pre-trim intermediate. `none` is zero post-threshold
  chunks AND zero KG edges (both reason constants); `weak` is not-none plus
  either a query-extracted identifier absent from every chunk/edge quote or
  exactly one retrieval half empty (reasons name the identifier(s)/empty
  half); `ok` is otherwise (`reasons: []`). See `mcp/RESPONSE_SHAPES.md`.
- Guards the issue #47 incident class, reproduced and live-verified in
  `mcp/tests/gold/PR5-EVIDENCE.md`: "What is the sn_zz_fake_table used for?"
  returns 5 on-topic chunks and 0 KG edges — a full-looking result — but the
  asked-for table doesn't exist anywhere in the content. `grounding` now
  classifies this `weak`, with reasons "identifier(s) sn_zz_fake_table not
  found in any returned content" and "KG returned nothing for this query",
  instead of silently handing back a padded-looking envelope. Live gates
  (battery stack, shipped defaults, warm cache): the nonsense-query probe
  classifies `none` (0/0 chunks/edges); fixed-11 and the identifier cohort
  (`pm_project`) stay `ok` (`reasons: []`, no fake identifiers in payload).
- The `enrich` tool description now instructs callers directly: 'On
  grounding.tier "none" or "weak", say what was not found rather than
  answering from prior knowledge — never present an identifier that is not
  in the returned content.'
- Regression tests pin the incident class
  (`test_enrich_grounding_weak_when_asked_identifier_absent`) and that
  classification happens on what the caller actually receives, not an
  earlier intermediate (`test_grounding_reflects_post_trim_not_pre_trim_content`
  — an order-swap mutant fails it).
- Scope: classification is observation-only — no selection, threshold, or
  lane behavior changed (diff touches enrich envelope assembly, the
  classifier module, the tool description, and tests only). `vector_search`
  is unchanged: no `grounding` key, no warnings channel — recorded follow-up
  from PR 4's review.

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
  tokens, the lexical lane is skipped for that call. `enrich`'s `warnings`
  gets the exact string `"lexical lane degraded — chunk_text index not
ready"` — never a silent drop when the caller might have expected a
  literal-token hit. `vector_search` has no `warnings` channel; it signals
  the same degradation only implicitly, via a dense-lane-only `results`
  list.
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
