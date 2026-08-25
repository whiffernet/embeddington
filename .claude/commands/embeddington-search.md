---
description: "Raw semantic search over the corpus — evidence, not synthesis"
argument-hint: "[search terms]"
allowed-tools: mcp__embeddington__vector_search, mcp__embeddington-local__vector_search
---

# Search embeddington

Run `vector_search` with **$ARGUMENTS** as `query`.

Show what came back rather than answering from it: for each hit, the score, the source
document, and enough of the chunk to judge it. The point of this command is to see the
evidence, so do not summarize it away.

If the top scores are low and clustered, say so — that pattern usually means nothing in
the corpus really matches, and it is more useful to the user than a confident paraphrase
of the least-bad hit. Use `/embeddington-ask` instead when they want an answer rather than sources.
