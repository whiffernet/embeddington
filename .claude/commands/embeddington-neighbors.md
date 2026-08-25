---
description: "Explore what an entity connects to, with depth and predicate filters"
argument-hint: "[entity name or id] [optional: predicate filter]"
allowed-tools: mcp__embeddington__kg_neighbors, mcp__embeddington-local__kg_neighbors, mcp__embeddington__kg_find_entities, mcp__embeddington-local__kg_find_entities, mcp__embeddington__kg_schema, mcp__embeddington-local__kg_schema
---

# Explore a neighbourhood

Explore the graph around: **$ARGUMENTS**

If given a name rather than an `entities_v2/…` id, resolve it with `kg_find_entities`
first, and say which entity you picked. Then call `kg_neighbors`.

**Choose the parameters deliberately — the defaults are not always right:**

- `depth` (1–3, default 1). Start at 1. Depth 2 from a high-degree entity can produce 80KB
  of JSON, which stops being readable and starts being a file to grep. Go deeper only when
  depth 1 didn't answer the question.
- `types` — predicate filter, and the main tool for making depth 2 usable. Call `kg_schema`
  **first** if you are filtering: predicates are a controlled vocabulary, and a guessed name
  silently returns nothing, which looks exactly like a genuinely empty neighbourhood.
- `limit` (default 100, max 500) caps raw traversal rows before dedup. Raise it only for
  deliberate broad exploration.

Report the neighbourhood grouped by predicate, so the shape of the entity's relationships
is visible rather than a flat list. Check `truncation` and say when the view is partial —
an unmentioned cut-off reads as "this is everything", which is the wrong impression to
leave.

Cite `source_quote` for any relationship you rely on, treat `inferred` or low-confidence
edges as tentative, and scope version-sensitive claims to each edge's `releases`.
