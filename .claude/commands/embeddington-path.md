---
description: "Show how two entities are connected, with the hub caveat applied"
argument-hint: "[entity A] [entity B]"
allowed-tools: mcp__embeddington__kg_find_entities, mcp__embeddington-local__kg_find_entities, mcp__embeddington__kg_path, mcp__embeddington-local__kg_path
---

# Path between entities

Find how these two things connect: **$ARGUMENTS**

Resolve both names to IDs with `kg_find_entities` first — `kg_path` takes `from_id` and
`to_id` as document IDs (`entities_v2/…`). Then call `kg_path`.

**Apply the hub caveat before narrating anything.** A path routed through a very
high-degree node usually means "both of these touch something popular", not "these two are
related". Measured on this graph, the large majority of paths between arbitrary entities
run through a handful of hubs, and for most such pairs there is no hub-free route at all —
the hub *is* the only connection. So:

- A short path over specific, meaningful predicates is a real relationship. Say so.
- A path that hops through an obvious hub is weak evidence. Say **that** instead of
  spinning a story out of it, and note which node is doing the connecting.
- `no_path` within `max_hops` is a legitimate answer. Report it rather than raising
  `max_hops` until something appears.

Cite each edge's `source_quote`, treat inferred edges as tentative, and scope claims to
the `releases` the edges name.
