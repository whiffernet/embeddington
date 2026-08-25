---
description: "Ask the knowledge graph a question and get a grounded answer"
argument-hint: "[question]"
allowed-tools: mcp__embeddington__enrich, mcp__embeddington-local__enrich
---

# Ask embeddington

Answer this question from the embeddington knowledge graph: **$ARGUMENTS**

Call the `enrich` tool with the question as `query`. It returns vector chunks, KG edges,
and a `grounding` object.

**Read `grounding.tier` before you write a word of the answer:**

- `"ok"` — answer normally, citing what came back.
- `"weak"` or `"none"` — say plainly what was **not** found, and answer only from what
  actually came back. `reasons` names the problem (an identifier absent from every result,
  or one retrieval half empty). Do not fall back on what you already know about the
  subject.

Never present an identifier — a table, plugin, property, or API name — that is not in the
returned content. A confident answer about something the graph does not contain is the one
failure mode this corpus exists to prevent.

Cite `source_quote` verbatim where you rely on it, and scope claims to the `releases` a
result names rather than stating them as universally true. Treat edges marked as inferred
as tentative.

If the question names a specific entity, pass it in `entity_hints` — that steers the KG
half of the retrieval rather than leaving it to fuzzy matching.
