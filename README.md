<p align="center">
  <img src="assets/dude-hero-02.png" width="760" alt="A relaxed fellow in a Pendleton cardigan doing a white-Russian spit-take at a glowing tablet full of knowledge-graph data.">
</p>

# embeddington

> _"Sometimes there's a graph — I won't say a hero, 'cause what's a hero? — but sometimes there's a graph that's just right for its time and place. It fits right in there. And that's embeddington, on your own machine."_

A shared **ServiceNow technology knowledge graph** that installs on your machine and keeps
itself current. The data abides.

Everything here is derived from one source of truth:
**[github.com/ServiceNow/ServiceNowDocs](https://github.com/ServiceNow/ServiceNowDocs)** —
ServiceNow's own platform documentation, published by ServiceNow under the Apache License
2.0. embeddington doesn't replace those docs and doesn't know anything they don't say. It
reads them, extracts the entities and relationships buried in the prose, and hands you the
result as something you can query and traverse. Every triple in the graph carries the
`source_document` it came from and the `source_quote` that produced it, so any claim walks
back to a real sentence in a real ServiceNow page.

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

## Why the juice is worth the squeeze (graph vs. search vs. the docs)

> _"You're not wrong, Walter. You're just an asshole."_ — being right about where the
> answer lives isn't the same as getting it out.

The ServiceNow docs are authoritative and public. So why build anything at all?

Because there are three different ways to ask a question, and they fail differently.

**Reading the docs directly** gives you the truth, one page at a time. It's perfect when you
know which page you need. It's brutal when the answer isn't written on any single page —
and across ~48,000 markdown files, most interesting answers aren't. You end up being the
join engine: open twelve tabs, hold the relationships in your head, hope you didn't miss a
thirteenth.

**Vector search (plain RAG)** finds passages that _resemble_ your question. Ask "what is a
MID Server" and it nails it, because some paragraph literally says what a MID Server is.
Ask "if we deprecate this integration, what else breaks?" and it hands you the five
passages most similar to the words _deprecate_ and _breaks_ — which is not the answer,
because **no passage contains the answer**. The answer only exists in the relationships
_between_ passages, and similarity search cannot traverse a relationship it never
represented. It retrieves; it doesn't connect.

**A knowledge graph** does the joining ahead of time. Extraction reads every page once and
writes down the relationships as typed edges — _this feature extends that one, this
component depends on that service, this plugin requires that other plugin_. Now the
multi-hop question is a two-line traversal instead of an afternoon, and the machine follows
the chain instead of guessing at it.

| Your question                                          | The docs                       | Vector search                      | The graph                |
| ------------------------------------------------------ | ------------------------------ | ---------------------------------- | ------------------------ |
| "What is a MID Server?"                                | ✅ if you find the page        | ✅ nails it                        | ✅ but overkill          |
| "Which components depend on the MID Server?"           | ⚠️ scattered across many pages | ⚠️ returns pages that _mention_ it | ✅ one hop, exhaustive   |
| "If we deprecate X, what breaks two steps downstream?" | ❌ you are the join engine     | ❌ no single passage says this     | ✅ two hops              |
| "Summarize how this feature actually behaves"          | ✅ the prose is the point      | ✅ retrieves the prose             | ⚠️ edges lose the nuance |

**So use both — that's the point.** embeddington ships the vectors _and_ the graph, and the
MCP server puts both in front of Claude at once. Claude traverses the graph to find _which_
things are connected, then pulls the actual passages to explain _how_. Structure from the
edges, nuance from the prose. Neither alone gets you there.

The honest caveat: extraction is derived data, and derived data is lossy. An edge is a
compression of a sentence, and compression throws things away. That's exactly why every
triple keeps its `source_document` and `source_quote` — when the graph says two things are
related, you can go read the sentence that said so and judge for yourself. The graph tells
you where to look. The docs are still the truth.

---

## By the numbers

> _"There's a lot of strands to keep in old Duder's head."_

Snapshot of the **`baseline-2026-07`** baseline (as of **2026-07-02**). The graph grows as
daily diffs land, so a fresh install will already be a touch bigger than this.

| Metric                                      | Count       |
| ------------------------------------------- | ----------- |
| Vectors (Qdrant chunks, `bge-m3`, 1024-dim) | **150,822** |
| Entities (graph nodes)                      | **309,773** |
| Relationships / triples (graph edges)       | **682,068** |
| Entity types                                | 14          |
| Relationship predicates                     | 14          |
| Avg. relationships per entity               | ~2.2        |

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
# run from: anywhere — this configures gh itself, not the repo
echo "YOUR_TOKEN" | gh auth login --with-token
```

That token can only **read this one repo** — you can pull the graph and its daily updates,
and nothing else. Everything below (clone, stack, `embeddington-consume update`) then works
exactly as written.

Prefer the token as an environment variable instead of `gh`? That works too — just
`export GITHUB_TOKEN=YOUR_TOKEN` before running `embeddington-consume update`.

> _"This aggression will not stand."_ One gotcha for hand-debuggers: a raw
> `curl https://github.com/whiffernet/embeddington/releases/download/...` with the token in
> an `Authorization` header returns **404** on a private repo — that download URL only
> honors browser sessions, not tokens. It's not a missing file. Use `gh` or the
> `embeddington-consume` CLI (both fetch via the GitHub API, which _does_ honor the token).

---

## Takin' 'er easy (install)

> _"The Dude abides."_

### First, know where you're standing

Almost every confusing moment with this repo comes from running a command in the wrong
directory. There are only **two** that matter, and each owns a different job:

| Directory       | What lives there                 | What you run there               |
| --------------- | -------------------------------- | -------------------------------- |
| **repo root**   | `pyproject.toml`, `src/`, `mcp/` | `pip install -e .`, `pytest`     |
| **`consumer/`** | `docker-compose.yml`, `.env`     | every `docker compose …` command |

Two rules that follow from the table, and cover ~all of it:

- **`docker compose` only works inside `consumer/`.** That's where the compose file is. Run
  it from the root and Docker will tell you it can't find a configuration file.
- **`embeddington-consume` works from anywhere**, once installed. It's a real command on
  your `PATH`, not a script you have to be next to. You never need to `cd` into `consumer/`
  to use it.

Every code block below starts with a `# run from:` comment. When in doubt, that's the
answer. `~/embeddington` is used as the example clone location — substitute your own.

### The steps

**1. Clone.** Use `gh` rather than `git`, so it reuses the login from above — they're
different tools with different credentials, and `gh repo clone` uses your GitHub auth
directly:

```bash
# run from: anywhere you keep code (e.g. ~)
gh repo clone whiffernet/embeddington
cd embeddington          # <- you are now at the REPO ROOT
```

**2. Start the local stack** (Qdrant + ArangoDB + the embedder). This is the one step that
must happen inside `consumer/`, because that's where `docker-compose.yml` lives:

```bash
# run from: repo root
cd consumer

cp .env.example .env      # then open .env and set ARANGO_ROOT_PASSWORD to anything you like
docker compose up -d      # <- must be run from consumer/

cd ..                     # <- back to the REPO ROOT for step 3
```

Check it came up before moving on:

```bash
# run from: consumer/
docker compose ps         # all services should read "running" / "healthy"
```

The `embed` service builds on first run and downloads the `bge-m3` model (~2 GB) the first
time it starts — that one-time pull is what powers semantic search.

> _"New information has come to light."_ That first build also compiles a CPU embedder,
> which pulls ~150 MB of PyTorch and takes **10–20 minutes** — Qdrant and ArangoDB are
> quick pre-built pulls, but the embedder is built locally so it runs on both Intel and
> Apple Silicon. **If the build times out** on a slow connection, just re-run
> `docker compose up -d --build` — Docker doesn't cache a failed layer, so the retry picks
> up cleanly. The Dude doesn't sweat a dropped download.

**3. Install the consumer CLI.** This one needs the **repo root** (where `pyproject.toml`
lives) — the `cd ..` in step 2 already put you there:

```bash
# run from: repo root
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .
```

Confirm it landed. From here on, `embeddington-consume` is a normal command on your `PATH`:

```bash
# run from: anywhere
embeddington-consume --help
```

> _"Obviously you're not a golfer."_ If that says `command not found`, you almost certainly
> ran `pip install -e .` from `consumer/` instead of the repo root, or you opened a new
> shell and forgot to re-activate the venv (`. .venv/bin/activate` from the repo root).

---

## New information has come to light (import & update)

> _"New information has come to light, man."_

One command restores the baseline on first run, then applies any newer diffs. Later runs
apply only what changed.

`embeddington-consume` needs `ARANGO_ROOT_PASSWORD` in its environment — the same value you
put in `consumer/.env` during install. The first line below loads it. That relative path
(`consumer/.env`) is why this block wants the repo root:

```bash
# run from: repo root
set -a; . consumer/.env; set +a       # loads ARANGO_ROOT_PASSWORD into this shell
embeddington-consume update --repo whiffernet/embeddington
```

Running it from somewhere else? Point at the file absolutely, and the command itself no
longer cares where you are:

```bash
# run from: anywhere
set -a; . ~/embeddington/consumer/.env; set +a
embeddington-consume update --repo whiffernet/embeddington
```

You only need the `set -a` line once per shell. For a cron job, keep both lines together —
cron starts a fresh shell with none of your environment:

```bash
# crontab -e   — update daily at 06:00
0 6 * * * set -a; . $HOME/embeddington/consumer/.env; set +a; $HOME/embeddington/.venv/bin/embeddington-consume update --repo whiffernet/embeddington >> $HOME/embeddington-update.log 2>&1
```

First run downloads and restores the full baseline (a few hundred MB), so it takes a few
minutes. After that, updates are tiny. Run it on whatever schedule you like (a daily cron,
say) to stay current.

What it prints. **First run** (or the first run after a new baseline is cut) restores the
whole graph:

```
Embeddington update complete.
  Action:  restored full baseline (baseline-2026-07)
  Loaded:  150,822 vectors · 309,773 entities · 682,068 edges
  Version: cb48b5c3e046f240aa0b7b9656c8505d6cbb98b7
  Diffs:   0 applied on top of the baseline
  Note:    a one-time full re-download is expected after a compaction — existing
           installs re-restore the latest snapshot in a single step.
```

**Later runs** apply only what changed, and say so when there's nothing to do:

```
Embeddington update complete.
  Action:  applied 3 incremental update(s)
  Version: 9f2a1c7e0b4d8a6f3e5c1b9d7a2f4e6c8b0d3a5f
```

```
Embeddington update complete.
  Action:  no changes — already the latest
  Version: 9f2a1c7e0b4d8a6f3e5c1b9d7a2f4e6c8b0d3a5f
```

A baseline restore reporting `Diffs: 0` is a **success**, not a no-op — it means the snapshot
it just loaded was already current. Nothing more to fetch, man.

---

## That tablet really ties the graph together (query with Claude)

> _"That RAG really tied the room together."_

`mcp/` is a stdio MCP server exposing vector search and graph traversal over your local
stores. The repo ships a project-scoped **`.mcp.json`** that Claude Code auto-discovers, so
the server appears as **embeddington** (its tools as `mcp__embeddington__…`) — no manual
endpoint wiring beyond having `ARANGO_ROOT_PASSWORD` set.

```bash
# run from: repo root — installs the MCP server's deps into your active venv
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

Plan for **~8.5 GB** once everything settles. Itemized:

| Component                                        | Disk    |
| ------------------------------------------------ | ------- |
| `bge-m3` model (first boot, in a volume)         | ~2.2 GB |
| `embed` service image (CPU-only torch)           | ~1.3 GB |
| Qdrant + ArangoDB engine images                  | ~0.7 GB |
| Restored graph (Qdrant ~2.4 GB + Arango ~0.9 GB) | ~3.3 GB |
| Baseline download (transient — deletable)        | ~0.9 GB |

Figure a little extra headroom during the first download — the compressed baseline and the
restored copy coexist until you clear `data/work/` — plus **~3–4 GB RAM** to run the embedder
and the two stores.

Sizes track the baseline, so they grow over time: `baseline-2026-07` roughly doubled the
vector count over `baseline-2026-06`, and the disk figures moved with it.

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

There are **two** suites, and they run from different directories. This isn't an oversight —
the MCP server's tests need `mcp/` as pytest's rootdir, because the repo has a directory
named `mcp/` that would otherwise shadow the official `mcp` SDK package it imports.

The main suite, from the repo root:

```bash
# run from: repo root
pip install -e ".[dev]"
pytest
```

The MCP server's suite, from `mcp/`:

```bash
# run from: mcp/
cd mcp
pip install -r requirements-dev.txt
pytest
cd ..
```

---

<p align="center"><em>The graph abides.</em></p>
