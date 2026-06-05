# claudeGraph MCP

Local stdio MCP server that exposes a Qdrant vector collection + ServiceNow
ArangoDB knowledge graph to Claude Desktop directly. Returns structured JSON
for Claude's LLM to synthesize — does NOT run a local LLM in the loop.

Designed for a setup where Claude Desktop runs on one machine (e.g. a Mac)
and the data services (Qdrant, ArangoDB, the LlamaIndex `/embed` endpoint)
run on another machine (e.g. a "spark" / dev box) reachable on the same LAN.

## What it does

Seven tools, all returning structured JSON (no synthesized prose):

| Tool                                      | Purpose                                                                                                                                                                                                                                         |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `enrich(query, entity_hints, top_k)`      | Default. Parallel vector search + KG entity match + 1-hop neighborhood.                                                                                                                                                                         |
| `vector_search(query, collection, limit)` | Raw Qdrant vector search against an allowlisted collection. `collection` defaults to `technology` (the ServiceNow MD corpus); the query is embedded with the encoder matching the chosen collection. `enrich` is always pinned to `technology`. |
| `kg_find_entities(text, limit)`           | Fuzzy entity name search.                                                                                                                                                                                                                       |
| `kg_get_entity(entity_id)`                | Full entity document.                                                                                                                                                                                                                           |
| `kg_neighbors(entity_id, depth, types)`   | Graph traversal.                                                                                                                                                                                                                                |
| `kg_path(from_id, to_id, max_hops)`       | Shortest path between two entities.                                                                                                                                                                                                             |
| `kg_schema()`                             | Catalog of entity types + relationship predicates.                                                                                                                                                                                              |

> **Consuming these tools?** See [`RESPONSE_SHAPES.md`](RESPONSE_SHAPES.md) — the authoritative, version-tracked contract for every tool's return shape (envelopes, edge/entity/node/chunk fields, error cases, and grounding guidance). The integration tests enforce it.

## Architecture

```
Claude Desktop
  └─> claudeGraph MCP (stdio subprocess on the Desktop machine)
        ├─> Qdrant (port 6333)         — code-scoped to allowlisted collections
        ├─> ArangoDB (port 8529)       — scoped read-only user
        └─> LlamaIndex /embed (8100)   — for query vectorization (1024-dim)
```

Security model:

- **ArangoDB**: real per-collection isolation via a dedicated read-only user with
  explicit `none` grants on every collection that isn't the ServiceNow KG.
- **Qdrant**: v1 uses code-level scoping (allowlist enforced in `config.py`);
  JWT-based credential isolation deferred until the broader Qdrant API key
  retrofit lands across LangChain + LlamaIndex + other consumers.

## Prerequisites

You need:

- Python 3.12+
- A reachable Qdrant instance with a `technology` collection
- A reachable ArangoDB with a `knowledge_graph` database containing the
  ServiceNow KG collections (`entities_v2`, `relationships_v2`) and a named
  graph `servicenow_graph_v2` wrapping them
- A reachable embedding endpoint (LlamaIndex `/embed`) producing 1024-dim vectors
- A scoped read-only ArangoDB user with grants on `entities_v2` and
  `relationships_v2` (and `none` on everything else)

## Setup (any platform — Mac, Linux, Windows)

**1. Clone the repo on the machine that will run Claude Desktop:**

```bash
git clone <repo-url>
cd claudegraph
python3 -m venv .venv
.venv/bin/pip install qdrant-client python-arango python-dotenv fastmcp httpx pydantic
```

(Or `pip install -e ".[dev]"` if the flat-layout install works on your machine.)

**2. Create `.env` with your connection details:**

Copy `.env.example` to `.env` and fill in the values. Then lock the file:

```bash
chmod 0600.env
```

**3. Register with Claude Desktop:**

Add a `claudegraph` block under `mcpServers` in your Claude Desktop config:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "claudegraph": {
      "command": "/path/to/.venv/bin/python",
      "args": ["/path/to/server.py"],
      "env": {}
    }
  }
}
```

Keep your existing MCPs — just merge the `claudegraph` block.

**4. Restart Claude Desktop fully.** Then in a new conversation: _"List the
tools available from claudegraph"_ — you should see all seven.

## Configuration (`.env` keys)

See `.env.example` for the full template.

| Key                   | Default                       | Notes                                                                |
| --------------------- | ----------------------------- | -------------------------------------------------------------------- |
| `QDRANT_URL`          | `http://localhost:6333`       | Use the LAN hostname if MCP runs on a different machine than Qdrant. |
| `ARANGO_URL`          | `http://localhost:8529`       | Same.                                                                |
| `ARANGO_DATABASE`     | `knowledge_graph`             |                                                                      |
| `ARANGO_USER`         | `arango_reader`               | Scoped read-only user; set up separately in ArangoDB.                |
| `ARANGO_PASSWORD`     | (none — required)             | Password for the scoped Arango user.                                 |
| `EMBED_URL`           | `http://localhost:8100/embed` | LlamaIndex /embed; must produce 1024-dim vectors.                    |
| `CLAUDEGRAPH_TIMEOUT` | `30`                          | HTTP timeout in seconds.                                             |

Process env vars (e.g. those set by `claude_desktop_config.json`) **override**
the `.env` file. So you can keep credentials in either place — `.env` for
the cleaner default, JSON env block if you prefer everything in one file.

## Adapting for non-ServiceNow KGs

The Qdrant collection (`technology`), the Arango collections (`entities_v2`,
`relationships_v2`), and the named graph (`servicenow_graph_v2`) are
currently hardcoded in `config.py`. If your KG has a different schema, edit
those constants — they're isolated to one place. A future version may
make them env-configurable; track that as a follow-up.

## Running tests

```bash
# Unit tests only (no external services needed):
.venv/bin/pytest claudegraph/tests/ -k "not integration and not arango"

# Full suite (unit + integration, requires live services):
ARANGO_TEST_PASSWORD=<your-arango-password> \
ARANGO_TEST_URL=http://localhost:8529 \
QDRANT_URL=http://localhost:6333 \
EMBED_URL=http://localhost:8100/embed \
  .venv/bin/pytest claudegraph/tests/
```

## Spec & plan

Full design at `docs/superpowers/specs/2026-05-09-claudegraph-mcp-design.md`.
Implementation plan at `docs/superpowers/plans/2026-05-09-claudegraph-implementation.md`.
