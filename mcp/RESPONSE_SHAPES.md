# embeddington — Response Shapes (consumer contract)

**Single source of truth for what every embeddington tool returns.** If you
consume embeddington (as a registered MCP, by importing the modules, or by
hand-rolling clients against the same data), diff your assumptions against this
file. It is versioned with the code, so `git pull` keeps it current.

- **Current as of:** `v0.3.7`
- **Executable spec:** the shapes here are asserted by
  `mcp/tests/` (notably `test_arango_client.py`,
  `test_tools.py`, `test_enrich.py`) — those tests are the enforcement; this
  doc is the human-readable mirror. If they disagree, the tests win and this
  doc is stale (please fix it).

Every tool returns a **stable envelope**: the documented keys are always
present, and an `error` string is added on failure (so consumers never need to
guard for missing keys).

---

## Tools → top-level envelopes

| Tool                                                  | Success envelope                                                          |
| ----------------------------------------------------- | ------------------------------------------------------------------------- |
| `enrich(query, entity_hints?, top_k=10)`              | `{vector_chunks: [chunk], kg_matches: [{entity, neighbors}], errors: {}}` |
| `vector_search(query, collection?, limit=10)`         | `{results: [chunk], count, collection}`                                   |
| `kg_find_entities(text, limit=10)`                    | `{entities: [entity], count}`                                             |
| `kg_get_entity(entity_id)`                            | `{entity: <full doc> \| null}`                                            |
| `kg_neighbors(entity_id, depth=1, types?, limit=100)` | `{nodes: [node], edges: [edge]}`                                          |
| `kg_path(from_id, to_id, max_hops=4)`                 | `{nodes: [node], edges: [path_edge]}`                                     |
| `kg_schema()`                                         | `{entity_types: [str], predicates: [str]}`                                |

**Error / edge cases (keys stay stable, `error` added):**

- `vector_search` unknown collection → `{results: [], count: 0, collection, error: "unknown collection '<x>'; allowed: [...]"}` (no client is constructed — the allowlist is the only Qdrant scope guard).
- `enrich` `errors` is a **dict keyed by side** (`qdrant` / `arango`), `{}` on full success. The two sides run in parallel and fail independently — you can get `vector_chunks` with an `arango` error present.
- `kg_get_entity` not found → `{entity: null, error: "entity not found"}`.
- `kg_path` no path → `{nodes: [], edges: [], no_path: true}` (distinct from `error`).
- `kg_neighbors` / `kg_path` / `kg_find_entities` failure → same keys + `error`.

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

### `entity` (kg_find_entities `entities[]` and enrich `kg_matches[].entity`)

```jsonc
{
  "id": "entities_v2/feature__threat_lookup_auto-extraction",
  "name": "Threat lookup auto-extraction",
  "type": "Feature",
  "source_documents": ["IT Service Management"], // first 5 only (some entities have 1000s)
  "releases": ["zurich"],
} // version context; ~41% of entities populated (else null)
```

- ⚠️ The legacy `description` key was **removed in v0.3.5** (it was empty corpus-wide).
- **Ordering (v0.3.7):** `find_entities` results are relevance-ranked — exact name match, then prefix, then substring; ties broken by graph degree (descending). So `entities[0]` is the core hub entity, not an arbitrary peripheral match. This is what `enrich` seeds KG traversal from.
- `kg_get_entity` returns the **full doc** instead — richer: `{id, canonical_key, name, type, source_documents, schema_version, updated_at, releases}`.

### `node` (kg_neighbors / kg_path `nodes[]`)

```jsonc
{
  "id": "entities_v2/role__sn_ti.read",
  "name": "sn_ti.read",
  "type": "Role",
  "releases": ["zurich"],
} // per-entity version context (added v0.3.5; null if unpopulated)
```

### `edge` (kg_neighbors `edges[]` and enrich `kg_matches[].neighbors.edges[]`)

```jsonc
{
  "id": "4941593",
  "source": "entities_v2/feature__threat_lookup_auto-extraction", // _from, full _id
  "target": "entities_v2/role__sn_ti.read", // _to, full _id
  "predicate": "REQUIRES_ROLE",
  "confidence": 0.95, // float 0–1
  "extraction_type": "explicit", // "explicit" | "inferred" | "explet"(dirty typo) — added v0.3.5
  "releases": ["zurich"], // ~33% of edges populated (else null) — added v0.3.5
  "source_document": "IT Service Management",
  "source_quote": "The Predictive Intelligence ... plugin activates these ...",
} // verbatim, <=240 chars
```

**Ordering (v0.3.7):** `kg_neighbors` edges come back **highest-`confidence` first**, so when `limit` truncates a large (hub) neighborhood it keeps the most-reliable edges rather than an arbitrary slice. `enrich` stays depth-1 (a real hub already yields hundreds–thousands of depth-1 edges); for true multi-hop "how does A connect to B", use `kg_path`.

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

## Field semantics & grounding guidance

Surfacing these fields only helps if the synthesizing prompt uses them:

- **`source_quote`** — verbatim extraction text, ≤240 chars. **Cite this** for any KG relationship you put in an answer. It's the high-value provenance field (`source_document` is often a coarse product name like `"IT Service Management"`).
- **`releases`** — ServiceNow release tags (e.g. `["zurich"]`). **Scope version-sensitive claims to it** — a Zurich-only relationship is not necessarily current. The #1 KG failure mode is asserting a release-specific fact as universal.
- **`extraction_type`** — `explicit` (directly stated) vs `inferred`. **Hedge inferred edges.** (Note: a `explet` typo value exists in some edges — treat as `explicit`; it's a data-quality item, surfaced as-is.)
- **`confidence`** — float 0–1 on `edge` (not on `path_edge`). Treat low-confidence edges as tentative.

---

## Size guards (why things are capped)

Consumers (Claude Code / Desktop) have a ~75–100 KB single-tool-result cap.
embeddington bounds responses by: `source_quote` truncated to 240 chars,
`source_documents` capped to the first 5, `kg_neighbors`/`kg_path` row counts
capped by `limit` (default 100 / max 500 for neighbors). Don't raise `limit`
on dense hub entities without `types` filtering.

---

## Routing notes (Qdrant)

- `vector_search(collection=...)` selects which allowlisted Qdrant collection to
  search; the query is automatically embedded with the matching encoder for that
  collection. Defaults to `technology` (the ServiceNow MD corpus).
- `enrich` and all KG tools always use the `technology` collection + the single
  shared ServiceNow graph (`entities_v2` / `relationships_v2` / `servicenow_graph_v2`).

---

_Changelog of shape-affecting releases: v0.3.0 (vector_search collection param),
v0.3.4 (edge `source_document` + `source_quote`), v0.3.5 (edge `releases` +
`extraction_type`; entity `description`→`source_documents`+`releases`; node
`releases`). See `pyproject.toml` / git tags for the current version._
