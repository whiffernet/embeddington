# embeddington MCP

> _"That tablet really tied the graph together."_

This is the bundled MCP server that lets Claude reach into your local
knowledge graph and actually look things up — the vectors, the entities, the
relationships, the whole rug. It hands Claude structured JSON. Claude does the
thinking. There's no other model in the loop, no external API, no cloud
service quietly phoning home. The Dude keeps it local, man.

You ask Claude a question; Claude calls these tools; the tools query your
Qdrant collection and your ServiceNow ArangoDB graph and return clean,
citable JSON; Claude reasons over it and answers. That's the whole story.

## What shows up in Claude

The server registers as **`embeddington`**. Its tools appear as
`mcp__embeddington__vector_search`, `mcp__embeddington__enrich`, and so on.

That display name comes straight from the config key, not from anything magic
in the code:

- **Claude Code** auto-discovers the `.mcp.json` at the repo root. The
  `embeddington` key under `mcpServers` is the name you'll see. Nothing else to
  do — open the repo, and Claude Code picks it up.
- **Claude Desktop** needs a manual entry. Add a block under `mcpServers` in
  your config (rename the key and you rename the server — keep it
  `embeddington` to match the docs):

```json
{
  "mcpServers": {
    "embeddington": {
      "command": "python",
      "args": ["mcp/server.py"],
      "env": {
        "QDRANT_URL": "http://localhost:6333",
        "ARANGO_URL": "http://localhost:8529",
        "ARANGO_DATABASE": "technology_kg",
        "ARANGO_USER": "root",
        "ARANGO_PASSWORD": "your-arango-password",
        "EMBED_URL": "http://localhost:8100/embed"
      }
    }
  }
}
```

Config files live at:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Keep your existing MCPs — just merge the `embeddington` block in. Then restart
Claude Desktop fully.

## Install the deps

```bash
pip install -r mcp/requirements.txt
```

That's `fastmcp`, `python-arango`, `python-dotenv`, `httpx`, and `pydantic`.
The Dude abides by a short requirements file.

## Environment variables

The server reads its connection details from the environment (set them in the
`.mcp.json` / Desktop `env` block, or a `.env` next to `server.py`). New
information is welcome to come to light here:

| Variable          | Example                       | What it points at                                                           |
| ----------------- | ----------------------------- | --------------------------------------------------------------------------- |
| `QDRANT_URL`      | `http://localhost:6333`       | Your Qdrant instance (holds the `technology` collection).                   |
| `ARANGO_URL`      | `http://localhost:8529`       | Your ArangoDB instance (holds the ServiceNow graph).                        |
| `ARANGO_DATABASE` | `technology_kg`               | The database with `entities_v2`, `relationships_v2`, `servicenow_graph_v2`. |
| `ARANGO_USER`     | `root`                        | The Arango user the server authenticates as.                                |
| `ARANGO_PASSWORD` | _(required)_                  | That user's password. No default — the server refuses to start without it.  |
| `EMBED_URL`       | `http://localhost:8100/embed` | The local embedding service, producing 1024-dim `bge-m3` vectors.           |

Use LAN hostnames instead of `localhost` if Claude runs on a different machine
than the data services.

## The tools

Seven of them. Maude would approve of the precise ones.

**`enrich` is the fullest and most robust tool in the box — start there.** A single call
runs vector search **and** graph traversal (entity match + 1-hop neighbors) in parallel and
hands Claude both the documents and the connected structure, so it has the most to reason
over in one shot. The other six are for targeted drill-downs once you know where you're
headed.

| Tool                                      | What it does                                                                                                          | Needs embed service? |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------- | -------------------- |
| `enrich(query, entity_hints, top_k)`      | **The default move** — vector search **and** KG entity match + 1-hop neighbors, in parallel. The richest single call. | Yes                  |
| `vector_search(query, collection, limit)` | Raw vector search against an allowlisted Qdrant collection (defaults to `technology`).                                | Yes                  |
| `kg_find_entities(text, limit)`           | Fuzzy-match entity names; relevance-ranked, hub entities win.                                                         | No                   |
| `kg_get_entity(entity_id)`                | Fetch one full entity document by its `_id`.                                                                          | No                   |
| `kg_neighbors(entity_id, depth, types)`   | Traverse connected entities + edges around a node (depth 1–3).                                                        | No                   |
| `kg_path(from_id, to_id, max_hops)`       | Shortest path between two known entities.                                                                             | No                   |
| `kg_schema()`                             | List the entity types and relationship predicates in the graph.                                                       | No                   |

`vector_search` and `enrich` embed the query first, so they need `EMBED_URL`
reachable. The `kg_*` traversal tools talk to ArangoDB only — pure graph, no
embeddings.

Every tool returns structured JSON, never prose. Edges carry their
`source_quote`, `confidence`, `extraction_type`, and `releases` so Claude can
cite verbatim, treat inferred edges as tentative, and scope version-sensitive
claims. The Stranger appreciates a man who shows his sources.

## The files in this folder

| File                  | Purpose                                                                                          |
| --------------------- | ------------------------------------------------------------------------------------------------ |
| `server.py`           | The MCP entry point — `FastMCP("embeddington")` and the seven `@mcp.tool` functions. Run this.   |
| `config.py`           | Env-loaded settings, the Qdrant collection allowlist, and the Arango collection/graph names.     |
| `embedding_client.py` | Async client for the `/embed` endpoint; turns a query into a 1024-dim vector.                    |
| `qdrant_client.py`    | Async Qdrant client, scoped to one allowlisted collection per instance.                          |
| `arango_client.py`    | Read-only ArangoDB client; all the KG traversal AQL lives here.                                  |
| `enrich.py`           | The parallel vector + KG fan-out behind the `enrich` tool.                                       |
| `requirements.txt`    | Python dependencies.                                                                             |
| `RESPONSE_SHAPES.md`  | The authoritative contract for every tool's return shape — read it if you're consuming the JSON. |

The Dude abides.
