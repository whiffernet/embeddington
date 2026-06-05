# Embeddington

Install a shared **ServiceNow technology knowledge graph** on your own machine and keep
it current with a single command.

The graph has two parts that stay in sync:

- **Qdrant** — a vector collection (`technology`) for semantic search over the docs.
- **ArangoDB** — an entity/relationship graph (`entities_v2` / `relationships_v2`) for
  structured traversal (what depends on what, what a feature extends, and so on).

You get the data, not a service: Embeddington ships the graph as a **baseline** plus small
daily **diffs** published to GitHub Releases. Your local copy restores the baseline once,
then pulls only what changed. Updates are idempotent and resumable — re-running is always
safe.

A bundled MCP server (in `mcp/`) lets Claude query the graph directly (vector search +
graph traversal). It uses your local stores and Claude for reasoning — there is no
dependency on any external model or API. When loaded, it appears in Claude as
**embeddington**.

---

## Prerequisites

- **Docker** (with the Compose plugin) — runs the local Qdrant + ArangoDB.
- **GitHub CLI** (`gh`), logged in: `gh auth login`. You must have been **added as a
  collaborator** on this repository — that is how access to the data is granted.
- **Python 3.12+**.

Everything below is cross-platform (Linux, macOS, Windows via WSL2) because the stores run
in Docker.

---

## Install

**1. Clone:**

```bash
git clone https://github.com/whiffernet/embeddington.git
cd embeddington
```

**2. Start the local Qdrant + ArangoDB:**

```bash
cd consumer
cp .env.example .env          # then edit .env and set ARANGO_ROOT_PASSWORD
docker compose up -d
cd ..
```

**3. Install the consumer CLI:**

```bash
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .
```

---

## Import and update

One command restores the baseline on first run, then applies any newer diffs. Later runs
apply only what changed:

```bash
# Loads ARANGO_ROOT_PASSWORD from consumer/.env
set -a; . consumer/.env; set +a

embeddington-consume update --repo whiffernet/embeddington
```

First run downloads and restores the full baseline (a few hundred MB), so it takes a few
minutes. After that, updates are tiny. Run it on whatever schedule you like (e.g. a daily
cron) to stay current.

What it prints:

```
update: baseline, applied 3, cursor 0ba98cd…    # first run: baseline + 3 diffs
update: diffs, applied 1, cursor a1b2c3d…        # later: just new diffs
update: up_to_date, applied 0, cursor a1b2c3d…   # nothing new
```

---

## Query with Claude (the `embeddington` MCP)

`mcp/` is a stdio MCP server exposing vector search and graph traversal over your local
stores. The repo ships a project-scoped **`.mcp.json`** that Claude Code auto-discovers, so
the server appears as **embeddington** (its tools as `mcp__embeddington__…`) — no manual
wiring of endpoints needed beyond having `ARANGO_ROOT_PASSWORD` set in your environment.

```bash
# Install the MCP server's dependencies into your active environment:
pip install -r mcp/requirements.txt
```

Then open this repo in Claude Code (or Claude Desktop) and approve the `embeddington` MCP
when prompted. See **`mcp/README.md`** for details and for Claude Desktop's JSON config.

> **Note — vector search needs an embedder.** The graph-traversal tools
> (`kg_find_entities`, `kg_neighbors`, `kg_path`, `kg_schema`, `kg_get_entity`) work out of
> the box against your local ArangoDB. `vector_search` additionally needs a `bge-m3`
> embedding endpoint (`EMBED_URL`) to encode your query with the same model the collection
> was built with; set `EMBED_URL` if you run one. See `mcp/README.md`.

---

## How updating works

- A **manifest** on the `diffs` release lists the current baseline and an ordered, SHA-
  chained list of diffs. The CLI tracks a local **cursor** (the last point it applied).
- On each run it computes the shortest path to current: restore the latest baseline if it
  has no usable cursor, otherwise apply the contiguous diffs after its cursor.
- Every download is checksum-verified, every write is keyed (upsert/delete by id), and the
  cursor only advances after a diff fully applies — so an interrupted run resumes cleanly.

## Configuration

`embeddington-consume update` flags (all optional except `--repo`):

| Flag                | Default                 | Purpose                            |
| ------------------- | ----------------------- | ---------------------------------- |
| `--repo`            | _(required)_            | `owner/name` of this releases repo |
| `--cursor`          | `data/.cursor`          | Local cursor file                  |
| `--work-dir`        | `data/work`             | Scratch dir for downloads          |
| `--qdrant-url`      | `http://localhost:6333` | Local Qdrant                       |
| `--collection`      | `technology`            | Qdrant collection name             |
| `--arango-url`      | `http://localhost:8529` | Local ArangoDB                     |
| `--arango-db`       | `technology_kg`         | Target database                    |
| `--arango-user`     | `root`                  | ArangoDB user                      |
| `--arango-password` | `$ARANGO_ROOT_PASSWORD` | ArangoDB password                  |

Auth uses `gh` by default. To use a token instead, set `GITHUB_TOKEN` to a token that can
read this repo.

## Run the tests

```bash
pip install -e .[dev]
pytest
```
