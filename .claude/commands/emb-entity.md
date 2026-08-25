---
description: "Look up an entity by name and show what it connects to"
argument-hint: "[entity name]"
---

# Entity lookup

Find and describe **$ARGUMENTS** in the knowledge graph.

The KG tools address entities by document ID (`entities_v2/…`), never by name, so this is
three calls, not one:

1. `kg_find_entities` with the name as `text`. If several plausible matches come back,
   show them and ask which one rather than silently picking the top hit — near-identical
   names are common in this corpus and guessing wrong sends the rest of the answer
   somewhere the user did not ask about.
2. `kg_get_entity` with the chosen `entity_id`, for the entity's own record.
3. `kg_neighbors` with the same `entity_id` at `depth=1`, for what it connects to.

Report the entity, then group its neighbours by predicate so the shape of its
relationships is visible at a glance. Go deeper than `depth=1` only if asked: depth is
capped at 3, and this graph gets very wide very fast.

If `kg_find_entities` returns nothing, say so — do not describe the thing from memory. An
absent entity is a real and useful answer.
