# embeddington — Response Shapes (consumer contract)

**Single source of truth for what every embeddington tool returns.** If you
consume embeddington (as a registered MCP, by importing the modules, or by
hand-rolling clients against the same data), diff your assumptions against this
file. It is versioned with the code, so `git pull` keeps it current.

- **Current as of:** `v0.3.0` (embeddington repo line — see `CHANGELOG.md` at
  the repo root, which is now the git-tag-synced authority going forward).
- Version tags sprinkled through this doc's body (`upstream v0.3.4`,
  `upstream v0.3.5`, `upstream v0.3.7`) predate embeddington's own version
  line — they record when a shape was introduced in the upstream server
  before it was vendored into this repo, and don't correspond to
  `embeddington` git tags. The `v0.3.0` above is this repo's own, first
  `CHANGELOG.md`-tracked release.
- **Executable spec:** the shapes here are asserted by
  `mcp/tests/` (notably `test_arango_client.py`,
  `test_tools.py`, `test_enrich.py`, `test_budget.py`) — those tests are the
  enforcement; this doc is the human-readable mirror. If they disagree, the
  tests win and this doc is stale (please fix it).

Every tool returns a **stable envelope**: the documented keys are always
present, and an `error` string is added on failure (so consumers never need to
guard for missing keys).

---

> ### ⚠️ Behavioral change (v0.3.0)
>
> `enrich` output is now budget-bounded: default ≤40 edges TOTAL (previously
> up to ~100 edges PER matched entity — 576 edges observed on a 3-hint
> query) and `top_k` default 5 (was 10). Same-name entity variants are
> grouped into one concept match. Dropped edges are explicit
> (`truncation`, `suggest`) — never silent. Callers wanting breadth should
> page with `kg_neighbors` (now also truncation-flagged). Edge grounding
> fields are untouched. The response-size ceiling is server config
> (`EMBEDDINGTON_MAX_RESPONSE_TOKENS`), not a tool parameter.

---

## Tools → top-level envelopes

| Tool                                                                 | Success envelope                                                                                                      |
| -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `enrich(query, entity_hints?, top_k=5, edge_budget=40, predicates?)` | `{vector_chunks: [chunk], kg_matches: [match], errors: {}, budget: {edge_budget, returned, truncated}, warnings: []}` |
| `vector_search(query, collection?, limit=10)`                        | `{results: [chunk], count, collection}`                                                                               |
| `kg_find_entities(text, limit=10)`                                   | `{entities: [entity], count}`                                                                                         |
| `kg_get_entity(entity_id)`                                           | `{entity: <full doc> \| null}`                                                                                        |
| `kg_neighbors(entity_id, depth=1, types?, limit=100)`                | `{nodes: [node], edges: [edge], truncation: {truncated, available, returned}}`                                        |
| `kg_path(from_id, to_id, max_hops=4)`                                | `{nodes: [node], edges: [path_edge]}`                                                                                 |
| `kg_schema()`                                                        | `{entity_types: [str], predicates: [str]}`                                                                            |

`enrich`'s `predicates` param (v0.3.0) is an optional relationship-predicate
allowlist scoping KG expansion — omit it unless you've already called
`kg_schema`. It's **case-insensitive** (the server upper-cases it before
use); unknown predicates are **not rejected**, just flagged — the call
still runs, and `warnings` gets `"unknown predicates (call kg_schema):
[...]"` listing them lowercased. Validation is skipped silently (no
warning either way) if `kg_schema` itself is unreachable — a transient
Arango outage degrades to "don't validate," not "reject everything."
Passing `predicates` also changes the basis for each match's
`truncation.available` from a `degree` sum to a `count_edges()` sum — see
the `match` sub-shape's basis-semantics note.

`enrich`'s top-level `budget` object: `edge_budget` echoes the requested
(or default) budget; `returned` is the total edge count across all
`kg_matches` after response-ceiling trimming (i.e. it reflects what you
actually got, not what was allocated); `truncated` is `true` if any
match's own `truncation.truncated` is `true`, or if the response-ceiling
trim (`EMBEDDINGTON_MAX_RESPONSE_TOKENS`) removed anything.

`enrich`'s top-level `warnings` is a flat list of free-text advisory
strings (not structured/keyed) — non-exhaustive examples: `"no entity
hints extracted — pass entity_hints for KG results"`, `"unknown
predicates (call kg_schema): [...]"`, `"response ceiling: vector chunks
trimmed"`, `"response exceeds ceiling even at floors — narrow with
predicates"`. Treat it as an advisory surface, not an error signal —
`warnings` can be non-empty even when `errors` is `{}`.

**Error / edge cases (keys stay stable, `error` added):**

- `vector_search` unknown collection → `{results: [], count: 0, collection, error: "unknown collection '<x>'; allowed: [...]"}` (no client is constructed — the allowlist is the only Qdrant scope guard).
- `enrich` `errors` is a **dict keyed by side** (`qdrant` / `arango`), `{}` on full success. The two sides run in parallel and fail independently — you can get `vector_chunks` with an `arango` error present. Total-KG-failure (e.g. `find_entities` itself unreachable) sets `errors.arango`; a single concept's expansion failing does not — see the `match.error` field instead, which scopes to that concept only.
- `kg_get_entity` not found → `{entity: null, error: "entity not found"}`.
- `kg_path` no path → `{nodes: [], edges: [], no_path: true}` (distinct from `error`).
- `kg_neighbors` / `kg_path` / `kg_find_entities` failure → same keys + `error` (for `kg_neighbors` this includes a stub `truncation: {truncated: false, available: null, returned: 0}`).

---

## Shared sub-shapes

### `chunk` (vector_search `results[]` and enrich `vector_chunks[]` — identical)

```jsonc
{
  "id": "c65c71ff-...",
  "score": 0.7256,
  "text": "# Submit an IoC Lookup request ...",   // full prose; never the raw _node_content blob
  "source": "github-sync",                         // COARSE — see note below
  "metadata": { "title": "...", "canonical_url": null, "source_uri": "...",
                "file_name": "...", "product": "...", "release": "australia", ... }
}
```

- `_node_content` is stripped — never present. `text` is always clean.
- **Don't cite `chunk["source"]`** — it's a coarse ingestion label (e.g. `"github-sync"`). Cite from `metadata`: prefer `metadata.title` + (`metadata.canonical_url` → `metadata.source_uri` → `metadata.file_name`). `canonical_url` can be `null`.

### `entity` (kg_find_entities `entities[]` and `match.variants[]`)

```jsonc
{
  "id": "entities_v2/feature__threat_lookup_auto-extraction",
  "name": "Threat lookup auto-extraction",
  "type": "Feature",
  "source_documents": ["IT Service Management"], // first 5 only (some entities have 1000s)
  "releases": ["zurich"], // version context; ~41% of entities populated (else null)
  "degree": 42, // graph 1-hop edge count, computed at seed time — added v0.3.0
}
```

- ⚠️ The legacy `description` key was **removed in upstream v0.3.5** (it was empty corpus-wide).
- **Ordering (upstream v0.3.7):** `find_entities` results are relevance-ranked — exact name match, then prefix, then substring; ties broken by graph degree (descending). So `entities[0]` is the core hub entity, not an arbitrary peripheral match. This is what `enrich` seeds KG traversal from.
- **`degree` (v0.3.0):** graph degree (1-hop edge count, any direction), computed once at `find_entities` time and carried through to `enrich`'s `match.variants[]`. It's the ranking tiebreaker for `find_entities` and, when an `enrich` call has no `predicates` filter, the estimate basis for `match.truncation.available` (see the `match` sub-shape below).
- `kg_get_entity` returns the **full doc** instead — richer: `{id, canonical_key, name, type, source_documents, schema_version, updated_at, releases}` (no `degree` — that field is only computed by `find_entities`'s ranking traversal).

### `match` (enrich `kg_matches[]`) — added v0.3.0, replaces the old `{entity, neighbors}` shape

```jsonc
{
  "concept": "cmdb", // normalized dedup key (casefolded, punctuation-collapsed name)
  "variants": [
    /* entity, ... */
  ], // same-name entities across types/hints, merged into one match; variants[0] = best-ranked (highest graph `degree`, ties broken by id)
  "nodes": [
    /* node, ... */
  ], // union of nodes across all variants' fetched neighborhoods
  "edges": [
    /* edge, ... */
  ], // budget-selected: predicate-diversity floor, then confidence fill
  "truncation": { "truncated": true, "available": 5000, "returned": 10 },
  "suggest": {
    // null unless truncated
    "kg_neighbors": {
      "entity_id": "entities_v2/...",
      "types": ["REQUIRES_ROLE", "..."],
      "limit": 100,
    },
    "multi_hop": "for dependency chains use kg_path(from_id, to_id)",
  },
  "error": null, // set on this concept's own Arango failure; other matches unaffected
}
```

All seven keys are **always present** — even when this concept's expansion failed (`nodes`/`edges` empty, `error` set) or the budget allocator gave it zero slots (`edges` empty, `truncation.truncated` reflects whether there was anything to expand — see below).

**Ordering:** the top-level `kg_matches[]` array is ordered by earliest contributing `entity_hints` index, then first-seen order within that index — i.e. concepts seeded (even partly) by your first hint sort before ones seeded only by later hints, matching `allocate_budget`'s relevance weighting. Within a match, `variants[0]` is the highest-`degree` variant (ties broken by id) — see above.

> **`truncation.available` is an estimate, not an exact count — never
> derive a dropped-edge count from it.** For a concept with no `predicates`
> filter, `available` is the sum of each variant's `degree` (a value
> computed once, earlier, at `find_entities` seed time). For a
> `predicates`-filtered concept, `available` is the sum of a separate
> `count_edges()` call per variant instead (degree doesn't reflect a
> predicate subset). Either way, `available` is computed by a _different_
> query than the one that actually fetches edges, on a live graph — it can
> legitimately disagree with what that later fetch returns.
>
> `truncation.truncated` and `truncation.returned` instead reflect the
> **actual fetch**: `returned` is the edge count after budget selection,
> and `truncated` is `true` when the fetched pool (before selection) held
> more edges than were kept — both numbers come from the _same_ fetch, so
> that comparison is exact. The one exception is a concept that received
> **zero budget slots** (nothing was fetched at all): there,
> `truncated = available > 0`, i.e. it falls back to the estimate because
> there is no fetch to compare against.
>
> **Consumers must not compute `available - returned` and treat it as an
> exact count of dropped edges** — the two fields are on different
> counting bases except in the unfiltered/no-predicate case, and even then
> they come from separate queries.

### `node` (kg_neighbors / kg_path `nodes[]` and `match.nodes[]`)

```jsonc
{
  "id": "entities_v2/role__sn_ti.read",
  "name": "sn_ti.read",
  "type": "Role",
  "releases": ["zurich"],
} // per-entity version context (added upstream v0.3.5; null if unpopulated)
```

### `edge` (kg_neighbors `edges[]` and `match.edges[]`)

```jsonc
{
  "id": "4941593",
  "source": "entities_v2/feature__threat_lookup_auto-extraction", // _from, full _id
  "target": "entities_v2/role__sn_ti.read", // _to, full _id
  "predicate": "REQUIRES_ROLE",
  "confidence": 0.95, // float 0–1
  "extraction_type": "explicit", // "explicit" | "inferred" | "explet"(dirty typo) — added upstream v0.3.5
  "releases": ["zurich"], // ~33% of edges populated (else null) — added upstream v0.3.5
  "source_document": "IT Service Management",
  "source_quote": "The Predictive Intelligence ... plugin activates these ...",
} // verbatim, <=240 chars
```

**Ordering (upstream v0.3.7):** `kg_neighbors` edges come back **highest-`confidence` first**, so when `limit` truncates a large (hub) neighborhood it keeps the most-reliable edges rather than an arbitrary slice. `match.edges[]` (enrich, v0.3.0) uses a different order: a predicate-diversity floor pass (best edge per distinct predicate) followed by a confidence-desc fill pass, so a minority predicate's one edge survives ahead of a majority predicate's twentieth. `enrich` stays depth-1 (a real hub already yields hundreds–thousands of depth-1 edges); for true multi-hop "how does A connect to B", use `kg_path`.

### `path_edge` (kg_path `edges[]`) — leaner than `edge`

```jsonc
{
  "source": "...",
  "target": "...",
  "predicate": "REQUIRES_ROLE",
  "extraction_type": "explicit",
  "releases": ["zurich"],
  "source_document": "...",
  "source_quote": "...",
} // NO id, NO confidence
```

---

## `kg_neighbors` truncation (v0.3.0)

```jsonc
{ "truncated": true, "available": 812, "returned": 100 }
```

- **`truncated`** — `true` when the raw traversal fetched `>= limit` rows (pre-dedup). This is the reliable "you're not seeing the whole neighborhood" signal regardless of `depth`/`types`.
- **`available`** — populated **only** for `depth=1` calls that also pass `types`: an extra `count_edges()` query gives an exact, same-basis depth-1 count for that predicate filter. In every other case (`types` omitted, or `depth > 1`) it's `null` — an unfiltered depth-1 count isn't a meaningful ceiling for a filtered `returned`, and a depth-1 count is meaningless against a multi-hop traversal's `returned`. `count_edges()` failing (a secondary enrichment query) degrades `available` to `null` rather than discarding the `nodes`/`edges` payload already fetched.
- **`returned`** — `len(edges)` actually in this response, post-dedup.

---

## Field semantics & grounding guidance

Surfacing these fields only helps if the synthesizing prompt uses them:

- **`source_quote`** — verbatim extraction text, ≤240 chars. **Cite this** for any KG relationship you put in an answer. It's the high-value provenance field (`source_document` is often a coarse product name like `"IT Service Management"`).
- **`releases`** — ServiceNow release tags (e.g. `["zurich"]`). **Scope version-sensitive claims to it** — a Zurich-only relationship is not necessarily current. The #1 KG failure mode is asserting a release-specific fact as universal.
- **`extraction_type`** — `explicit` (directly stated) vs `inferred`. **Hedge inferred edges.** (Note: a `explet` typo value exists in some edges — treat as `explicit`; it's a data-quality item, surfaced as-is.)
- **`confidence`** — float 0–1 on `edge` (not on `path_edge`). Treat low-confidence edges as tentative.
- **`degree`** — graph 1-hop edge count on `entity`. A cheap "how big is this neighborhood" signal before you spend a `kg_neighbors` call; also the estimate basis behind `match.truncation.available` for unfiltered `enrich` concepts (see the basis-semantics note under the `match` sub-shape).

---

## Size guards (why things are capped)

Consumers (Claude Code / Desktop) have a ~75–100 KB single-tool-result cap.
embeddington bounds responses by:

- `source_quote` truncated to 240 chars, `source_documents` capped to the first 5.
- `kg_neighbors`/`kg_path` row counts capped by `limit` (default 100 / max 500 for neighbors). Don't raise `limit` on dense hub entities without `types` filtering.
- `enrich`, since v0.3.0, caps `top_k` (vector chunks, 1–50, default **5**, was 10) and `edge_budget` (KG edges **total across the whole response**, 1–200, default **40**, was ~100 _per matched entity_ uncapped in aggregate). The budget is allocated across matched concepts with relevance weighting and a per-concept floor; see the behavioral-change callout at the top of this doc. The default of 40 is the sweep knee (`mcp/tests/battery_results/2026-07-17-sweep.md`): under the response ceiling, edge delivery peaks near `edge_budget≈40` and then **inverts** — a larger budget overshoots the ceiling and the trim floors concepts, so asking for more edges can return fewer. Raise `edge_budget` past ~40–60 only with that caveat; for maximal KG grounding prefer lowering `top_k` (to 3), which cedes more of the shared ceiling to KG edges.
- A server-side response-token ceiling (`EMBEDDINGTON_MAX_RESPONSE_TOKENS`, default `12000` estimated tokens at ~3 chars/token — deliberately pessimistic) trims the _whole_ `enrich` response deterministically (KG edges from the largest match first, then vector chunks, always respecting per-concept floors) if it's still too large after budgeting. This is server config, not a tool parameter — callers cannot raise `edge_budget` past what the ceiling allows.

---

## Routing notes (Qdrant)

- `vector_search(collection=...)` selects which allowlisted Qdrant collection to
  search; the query is automatically embedded with the matching encoder for that
  collection. Defaults to `technology` (the ServiceNow MD corpus).
- `enrich` and all KG tools always use the `technology` collection + the single
  shared ServiceNow graph (`entities_v2` / `relationships_v2` / `servicenow_graph_v2`).

---

_Changelog of shape-affecting releases: upstream v0.3.0 (vector_search collection
param), upstream v0.3.4 (edge `source_document` + `source_quote`), upstream
v0.3.5 (edge `releases` + `extraction_type`; entity `description`→
`source_documents`+`releases`; node `releases`) — legacy references predating
embeddington's own version line, see the note under "Current as of" above._

_embeddington `v0.3.0`: `enrich` response-level `edge_budget` (concept dedup,
predicate-diversity selection, explicit `truncation`/`suggest`, per-concept
error scoping, `predicates` filter), server-side `EMBEDDINGTON_MAX_RESPONSE_TOKENS`
ceiling, `kg_neighbors` explicit `truncation`, entity `degree`. Full details in
`CHANGELOG.md` (repo root) — that file, plus `pyproject.toml` / git tags, is
the current, tag-tracked version history going forward._
