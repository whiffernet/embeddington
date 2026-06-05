<p align="center">
  <img src="assets/dude-hero.jpg" width="760" alt="A relaxed fellow in a Pendleton cardigan doing a white-Russian spit-take at a glowing tablet full of knowledge-graph data.">
</p>

# Embeddington

> _"Sometimes there's a graph — I won't say a hero, 'cause what's a hero? — but sometimes there's a graph that's just right for its time and place. It fits right in there. And that's Embeddington, on your own machine."_
> — The Stranger

A shared **ServiceNow technology knowledge graph** that installs on your machine and keeps
itself current. The data abides.

It comes in two parts that stay in sync:

- **Qdrant** — a vector collection (`technology`) for semantic search over the docs.
- **ArangoDB** — an entity/relationship graph (`entities_v2` / `relationships_v2`) for
  structured traversal: what depends on what, what a feature extends, the whole tied-together rug of it.

You get the data, not a service. Embeddington ships the graph as a **baseline** plus small
daily **diffs** on GitHub Releases. Your copy restores the baseline once, then pulls only
what changed — idempotent and resumable, so re-running is always safe. Real easy. Just
takin' it easy for all us data sinners.

A bundled MCP server (`mcp/`) lets Claude query the graph directly — vector search and
graph traversal, reasoned over by Claude, with no dependency on any outside model or API.
Loaded in Claude, it shows up as **embeddington**.

---

## There are rules (prerequisites)

> _"This is not 'Nam. This is bowling. There are rules."_ — Walter

- **Docker** (with the Compose plugin) — runs the local Qdrant + ArangoDB + embedder.
- **GitHub CLI** (`gh`), logged in (`gh auth login`). You must have been **added as a
  collaborator** on this repo — that's how access to the data is granted.
- **Python 3.12+**.

Cross-platform: Linux, macOS (Intel **and** Apple Silicon), and Windows via WSL2 — the
stores and the embedder all run in Docker.

---

## Takin' 'er easy (install)

> _"The Dude abides."_

**1. Clone:**

```bash
git clone https://github.com/whiffernet/embeddington.git
cd embeddington
```

**2. Start the local stack (Qdrant + ArangoDB + the embedder):**

```bash
cd consumer
cp .env.example .env          # then edit .env and set ARANGO_ROOT_PASSWORD
docker compose up -d
cd ..
```

The `embed` service builds on first run and downloads the `bge-m3` model (~2 GB) the first
time it starts — that one-time pull is what powers semantic search.

**3. Install the consumer CLI:**

```bash
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .
```

---

## New information has come to light (import & update)

> _"New information has come to light, man."_ — The Dude

One command restores the baseline on first run, then applies any newer diffs. Later runs
apply only what changed:

```bash
# Loads ARANGO_ROOT_PASSWORD from consumer/.env
set -a; . consumer/.env; set +a

embeddington-consume update --repo whiffernet/embeddington
```

First run downloads and restores the full baseline (a few hundred MB), so it takes a few
minutes. After that, updates are tiny. Run it on whatever schedule you like (a daily cron,
say) to stay current.

What it prints:

```
update: baseline, applied 3, cursor 0ba98cd…    # first run: baseline + 3 diffs
update: diffs, applied 1, cursor a1b2c3d…        # later: just new diffs
update: up_to_date, applied 0, cursor a1b2c3d…   # nothing new, man
```

---

## That tablet really ties the graph together (query with Claude)

> _"That rug really tied the room together."_ — The Dude

`mcp/` is a stdio MCP server exposing vector search and graph traversal over your local
stores. The repo ships a project-scoped **`.mcp.json`** that Claude Code auto-discovers, so
the server appears as **embeddington** (its tools as `mcp__embeddington__…`) — no manual
endpoint wiring beyond having `ARANGO_ROOT_PASSWORD` set.

```bash
# Install the MCP server's dependencies into your active environment:
pip install -r mcp/requirements.txt
```

Then open this repo in Claude Code (or Claude Desktop) and approve the `embeddington` MCP
when prompted. See **`mcp/README.md`** for details and Claude Desktop's JSON config.

Both query styles work out of the box: graph traversal (`kg_find_entities`, `kg_neighbors`,
`kg_path`, `kg_schema`, `kg_get_entity`) runs against your local ArangoDB, and
`vector_search` / `enrich` use the local `embed` service — the same `bge-m3` model the
collection was built with, so a query lands in the exact vector space of the data. No
outside embedding API. The `.mcp.json` already points `EMBED_URL` at it.

---

## How much room you'll need (storage)

> _"You want a toe? I can get you a toe… with disk space. Believe me."_ — Walter (more or less)

Plan for **~6 GB** once everything settles. Itemized:

| Component                                       | Disk    |
| ----------------------------------------------- | ------- |
| `bge-m3` model (first boot, in a volume)        | ~2.2 GB |
| `embed` service image (CPU-only torch)          | ~1.3 GB |
| Qdrant + ArangoDB engine images                 | ~0.6 GB |
| Restored graph (Qdrant ~1 GB + Arango ~0.55 GB) | ~1.6 GB |
| Baseline download (transient — deletable)       | ~0.5 GB |

Figure a little extra headroom during the first download, plus **~3–4 GB RAM** to run the
embedder and the two stores. The baseline download in `data/work/` can be cleared once the
restore finishes.

---

## What's in the box (how updating works)

> _"The word you're looking for is 'Yes.'"_ — Maude

- A **manifest** on the `diffs` release lists the current baseline and an ordered,
  SHA-chained list of diffs. The CLI tracks a local **cursor** (the last point it applied).
- On each run it computes the shortest path to current: restore the latest baseline if it
  has no usable cursor, otherwise apply the contiguous diffs after its cursor.
- Every download is checksum-verified, every write is keyed (upsert/delete by id), and the
  cursor only advances after a diff fully applies — so an interrupted run resumes cleanly.
  This aggression toward data loss will not stand.

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

> _"This is what happens when you float your version tags."_ — Walter
>
> The `docker-compose.yml` pins Qdrant to the exact version that produced the snapshot —
> Qdrant snapshot restore is version-sensitive. Don't float it to `:latest`.

## Run the tests

> _"Mark it zero."_ — Walter

```bash
pip install -e .[dev]
pytest
```

---

<p align="center"><em>The graph abides.</em></p>
