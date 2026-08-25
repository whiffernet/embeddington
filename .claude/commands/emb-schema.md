---
description: "List the knowledge graph's entity types and predicate vocabulary"
---

# Graph vocabulary

Call `kg_schema` and report what the graph actually contains: the entity types and the
predicate vocabulary, with counts where they are given.

This is the answer to "what can I even ask for?". Predicates are a controlled vocabulary —
a query written against a predicate that does not exist returns nothing, and looks
identical to a query about something the graph has never heard of. When the user is
hunting for a relationship, point them at the closest predicates that exist rather than
the one they guessed.
