---
description: "Find what something is actually called in the knowledge graph"
argument-hint: "[name or partial name]"
allowed-tools: mcp__embeddington__kg_find_entities, mcp__embeddington-local__kg_find_entities
---

# Find entities by name

Call `kg_find_entities` with **$ARGUMENTS** as `text`, then report every match rather than
collapsing them to one.

This answers "what is this called in here?", which is a real question in a corpus where the
same product name exists as several distinct entities. For each match show:

- **name and type** — the same name commonly appears as more than one type, and they are
  genuinely different nodes with different neighbourhoods.
- **`id`** — this is what `/embeddington-entity`, `/embeddington-neighbors`, and
  `/embeddington-path` need. The KG tools address entities by document ID, never by name.
- **`degree`** — how connected it is. A very high degree marks a hub, which matters later:
  paths through hubs are weak evidence, and unfiltered traversal from one is enormous.
- **`releases`** — which releases the entity was seen in.

If nothing comes back, say so plainly. An absent entity is a real answer, and it is more
useful than a description assembled from memory. Try a shorter or differently-spelled
fragment before concluding it isn't there — matching is on the name text.
