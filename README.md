<p align="center">
  <img src="assets/dude-hero-02.png" width="760" alt="A relaxed fellow in a Pendleton cardigan doing a white-Russian spit-take at a glowing tablet full of knowledge-graph data.">
</p>

# embeddington

> _"Sometimes there's a graph — I won't say a hero, 'cause what's a hero? — but sometimes there's a graph that's just right for its time and place. It fits right in there. And that's embeddington, on your own machine."_

A shared **ServiceNow technology knowledge graph** that installs on your machine and keeps
itself current. The data abides.

It comes in two parts that stay in sync:

- **Qdrant** — a vector collection (`technology`) for semantic search over the docs.
- **ArangoDB** — an entity/relationship graph (`entities_v2` / `relationships_v2`) for
  structured traversal: what depends on what, what a feature extends, the whole tied-together rug of it.

You get the data, not a service. embeddington ships the graph as a **baseline** plus small
daily **diffs** on GitHub Releases. Your copy restores the baseline once, then pulls only
what changed — idempotent and resumable, so re-running is always safe. Real easy. Just
takin' it easy for all us data sinners.

A bundled MCP server (`mcp/`) lets Claude query the graph directly — vector search and
graph traversal, reasoned over by Claude, with no dependency on any outside model or API.
Loaded in Claude, it shows up as **embeddington**.

---

## By the numbers

> _"There's a lot of strands to keep in old Duder's head."_

Snapshot of the **`baseline-2026-06`** baseline (as of **2026-06-04**). The graph grows as
daily diffs land, so a fresh install will already be a touch bigger than this.

| Metric                                      | Count       |
| ------------------------------------------- | ----------- |
| Vectors (Qdrant chunks, `bge-m3`, 1024-dim) | **62,717**  |
| Entities (graph nodes)                      | **242,937** |
| Relationships / triples (graph edges)       | **499,836** |
| Entity types                                | 14          |
| Relationship predicates                     | 14          |
| Avg. relationships per entity               | ~2.1        |

Each edge is one subject–predicate–object triple, so "relationships" and "triples" are the
same count. Distance metric is cosine; chunking is ~1500 tokens / 200 overlap.

---

## There are rules (prerequisites)

> _"This is not Docs. This is embeddington. There are rules."_

- **Docker** (with the Compose plugin) — runs the local Qdrant + ArangoDB + embedder.
- **GitHub CLI** (`gh`), authenticated with **read access** to this repo — either you've
  been added as a collaborator (`gh auth login`) or you were handed a **read-only token**
  (see below).
- **Python 3.12+**.

Cross-platform: Linux, macOS (Intel **and** Apple Silicon), and Windows via WSL2 — the
stores and the embedder all run in Docker.

---

## Got a read-only key? (token access)

> _"Far out."_

If someone shared a **read-only access token** with you instead of adding you as a
collaborator, just point `gh` at it once:

```bash
echo "YOUR_TOKEN" | gh auth login --with-token
```

That token can only **read this one repo** — you can pull the graph and its daily updates,
and nothing else. Everything below (clone, stack, `embeddington-consume update`) then works
exactly as written.

---

## Takin' 'er easy (install)

> _"The Dude abides."_

**1. Clone** (with `gh`, so it reuses the auth from above — `gh` and `git` are different
tools, and `gh repo clone` uses your GitHub login directly):

```bash
gh repo clone whiffernet/embeddington
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

> _"New information has come to light."_ That first build also compiles a CPU embedder,
> which pulls ~150 MB of PyTorch and takes **10–20 minutes** — Qdrant and ArangoDB are
> quick pre-built pulls, but the embedder is built locally so it runs on both Intel and
> Apple Silicon. **If the build times out** on a slow connection, just re-run
> `docker compose up -d --build` — Docker doesn't cache a failed layer, so the retry picks
> up cleanly. The Dude doesn't sweat a dropped download.

**3. Install the consumer CLI** — run this from the **repo root** (where `pyproject.toml`
lives), not from `consumer/`. The `cd ..` above already put you there. Once installed, the
`embeddington-consume` command is on your PATH and runs from anywhere — you never need to
`cd` into `consumer/` to use it.

```bash
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .
```

---

## New information has come to light (import & update)

> _"New information has come to light, man."_

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

> _"That rug really tied the room together."_

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

## Take 'er for a spin (example prompts)

> _"This is a very complicated case. A lotta ins, a lotta outs, a lotta what-have-yous."_

With the embeddington MCP loaded, ask Claude the kind of deep, multi-hop ServiceNow
architecture questions that need the graph **and** the docs together. Two examples:

**1. CI identification & deduplication strategy**

> A customer populates CMDB from Discovery, a Service Graph Connector, and a legacy import,
> and is accumulating duplicate CIs. Give a decision framework to reconcile identification:
> when to rely on Discovery identification rules vs connector-provided identifiers vs custom
> IRE rules, how datasource precedence resolves conflicting attribute ownership, the criteria
> for dependent vs independent CI identification, and the governance to prevent future
> duplication. Recommend a default authoritative-source model and name the exceptions.

**2. Multi-instance platform & domain strategy at scale**

> A global enterprise with 12 business units and 200k+ employees must choose its platform
> topology: single instance with domain separation vs separate production instances vs a
> hub-and-spoke model, the table-rotation/archiving strategy for high-volume tables, the
> cross-instance integration pattern, the performance levers, the license implications, and
> the top 3 architectural risks. Constraint: per-BU isolation with a shared CMDB, GA features
> only.

**Start with `enrich`** — it's the fullest, most robust tool in the box. One call runs
vector search **and** graph traversal (entity match + neighbors) in parallel and hands Claude
both, so it has the documents _and_ the connected structure to reason over. The other `kg_*`
tools are there for when you want to drill into one specific entity or trace a single path.

---

## How much room you'll need (storage)

> _"You want a toe? I can get you a toe… with disk space. Believe me."_

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

> _"The word you're looking for is 'Yes.'"_

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

> _"This is what happens when you float your version tags."_
>
> The `docker-compose.yml` pins Qdrant to the exact version that produced the snapshot —
> Qdrant snapshot restore is version-sensitive. Don't float it to `:latest`.

## Run the tests

> _"Mark it zero."_

```bash
pip install -e .[dev]
pytest
```

---

<p align="center"><em>The graph abides.</em></p>
