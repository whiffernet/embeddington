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
what changed — idempotent, and resumable at _diff_ granularity — an interrupted baseline
download restarts that one asset from zero (it streams to disk, so it won't eat your RAM
doing it). Real easy. Just takin' it easy for all us data sinners.

A bundled MCP server (`mcp/`) lets Claude query the graph directly — vector search and
graph traversal, reasoned over by Claude, with no dependency on any outside model or API.
Loaded in Claude, it shows up as **embeddington**.

---

## Why the juice is worth the squeeze (graph vs. search vs. the docs)

> _"You're not wrong, Walter. You're just unindexed."_ — being right about where the
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

Snapshot of the **`baseline-2026-07b`** baseline (as of **2026-07-09**). The graph grows as
daily diffs land, so a fresh install will already be a touch bigger than this.

| Metric                                      | Count       |
| ------------------------------------------- | ----------- |
| Vectors (Qdrant chunks, `bge-m3`, 1024-dim) | **152,194** |
| Entities (graph nodes)                      | **310,364** |
| Relationships / triples (graph edges)       | **683,651** |
| Entity types                                | 14          |
| Relationship predicates                     | 14          |
| Avg. relationships per entity               | ~2.2        |

Each edge is one subject–predicate–object triple, so "relationships" and "triples" are the
same count. Distance metric is cosine; chunking is ~1500 tokens / 200 overlap.

---

## Before you roll (prerequisites)

> _"This is not Docs. This is embeddington. There are rules."_

- **Docker** (with the Compose plugin) — runs the local Qdrant + ArangoDB + embedder.
- **Python 3.12+**.

That's the whole list. No account, no token, no access request — the download is a plain
HTTPS GET.

Cross-platform: Linux, macOS (Intel **and** Apple Silicon), and Windows via WSL2 — the
stores and the embedder all run in Docker.

---

## Takin' 'er easy (install)

> _"The Dude abides."_

One command. It checks your machine, offers to set up Docker if you don't have it
(OrbStack/Colima on macOS), starts the local stack, imports the knowledge graph, and
verifies it — interactively, with taste:

```bash
curl -fsSL https://raw.githubusercontent.com/whiffernet/embeddington/main/install.sh | bash
```

- Re-running it later is safe: on an installed machine it offers **Update / Repair /
  Uninstall** instead.
- Unattended (CI, scripts): `EMBEDDINGTON_YES=1`, install dir via
  `EMBEDDINGTON_INSTALL_DIR`. Unattended mode never installs Docker (it can't consent)
  and never deletes data.
- Prefer to read before you pipe? [`install.sh`](install.sh) is ~150 boring lines; the
  interesting parts run from the versioned clone after it.
- Health check any time: `embeddington-setup --check`

<details><summary>Manual install (the long way)</summary>

Prefer to run each step yourself instead of piping the one-liner? Here's what it does,
broken out.

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
  your `PATH`, and it keeps its bookkeeping in one per-user state directory
  (`~/.local/share/embeddington/`, or `$EMBEDDINGTON_HOME` if you set it) — not in whatever
  folder you happen to be standing in. You never need to `cd` into `consumer/` to use it.

Every code block below starts with a `# run from:` comment. When in doubt, that's the
answer. `~/embeddington` is used as the example clone location — substitute your own.

### The steps

**1. Clone.**

```bash
# run from: anywhere you keep code (e.g. ~)
git clone https://github.com/whiffernet/embeddington.git
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
docker compose ps   # all services should read "running" — the embed service keeps building/downloading for a while after
```

The `embed` service builds on first run and downloads the `bge-m3` model (~2 GB) the first
time it starts — that one-time pull is what powers semantic search.

> _"Sometimes you eat the bar, and sometimes, well, the bar eats you."_ That first build
> also compiles a CPU embedder, which pulls ~150 MB of PyTorch and takes **10–20 minutes**
> — Qdrant and ArangoDB are quick pre-built pulls, but the embedder is built locally so it
> runs on both Intel and Apple Silicon. **If the build times out** on a slow connection,
> just re-run `docker compose up -d --build` — Docker doesn't cache a failed layer, so the
> retry picks up cleanly. The Dude doesn't sweat a dropped download.

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

</details>

---

## Roll it forward (import & update)

> _"New information has come to light, man."_

One command restores the baseline on first run, then applies any newer diffs. Later runs
apply only what changed.

`embeddington-consume` needs `ARANGO_ROOT_PASSWORD` in its environment — the same value you
put in `consumer/.env` during install. The first line below loads it. That relative path
(`consumer/.env`) is why this block wants the repo root:

```bash
# run from: repo root
set -a; . consumer/.env; set +a       # loads ARANGO_ROOT_PASSWORD into this shell
embeddington-consume update
```

Running it from somewhere else? Point at the `.env` absolutely — the command itself no longer
cares where you are, because its cursor lives in the state directory, not the current one:

```bash
# run from: anywhere
set -a; . ~/embeddington/consumer/.env; set +a
embeddington-consume update
```

You only need the `set -a` line once per shell. For a cron job, keep both lines together —
cron starts a fresh shell with none of your environment, so you still need to source
`.env` there. Leading with a `cd` into the clone costs nothing and is a second, independent
guarantee that a first-time migration finds that clone's old cursor to adopt:

```bash
# crontab -e   — update daily at 06:00
0 6 * * * cd $HOME/embeddington && set -a && . consumer/.env && set +a && .venv/bin/embeddington-consume update >> $HOME/embeddington-update.log 2>&1
```

That example line assumes `~/embeddington`; if you installed somewhere else, the wizard's
receipt prints the crontab line for your actual install location — copy it from there
instead of hand-editing the path above.

First run downloads and restores the full baseline (a few hundred MB), so it takes a few
minutes. After that, updates are tiny. Run it on whatever schedule you like (a daily cron,
say) to stay current.

What it prints. **First run** (or the first run after a new baseline is cut) restores the
whole graph:

```
Embeddington update complete.
  Action:  restored full baseline (baseline-2026-07b)
  Loaded:  152,194 vectors · 310,364 entities · 683,651 edges
  Version: fd852b53bb07998ddc8e385971c25b94028fdf62
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

## Leaving town (uninstall)

> _"Sometimes there's a man... sometimes, there's a man."_

Same command, or `embeddington-setup --uninstall` from the clone. It shows everything
embeddington owns (containers, volumes, state dir, cron line, the clone), asks about
each item separately — every default is **No** — and looks _inside_ the stores first:
if it finds collections or databases you created, it names them and refuses to offer
volume deletion until you acknowledge. The knowledge-graph volumes require typing
`delete`; a plain `y` won't do it. Shared infrastructure (Docker, OrbStack, Colima,
Homebrew) is never removed — the receipt lists the manual commands if you want them
gone too.

---

## In the parlance of our times (query with Claude)

> _"You're entering a world of context."_

`mcp/` is a stdio MCP server exposing vector search and graph traversal over your local
stores. The repo ships a project-scoped **`.mcp.json`** that Claude Code auto-discovers, so
the server appears as **embeddington** (its tools as `mcp__embeddington__…`) — no manual
endpoint wiring beyond having `ARANGO_ROOT_PASSWORD` set.

The one bit of wiring: the server needs `ARANGO_ROOT_PASSWORD` in the environment Claude
launches it from — a GUI app doesn't inherit your shell's exports. Easiest path:

```bash
# run from: repo root — launch Claude Code with the password loaded
set -a; . consumer/.env; set +a
claude
```

For Claude Desktop, put the value in `mcp/.env` instead (`cp mcp/.env.example mcp/.env`,
fill in `ARANGO_PASSWORD`) — `server.py` reads it at startup.

> _"This is a private residence, man."_ `.mcp.json` connects as `ARANGO_USER: root`. That's
> **your own** ArangoDB container — the one `consumer/docker-compose.yml` started, with the
> password you chose in `consumer/.env`. No shared credential ships with this repo, and
> nothing here reaches a database you don't own.

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
architecture questions that need the graph **and** the docs together. Talk to it like you'd
talk to a colleague who's read everything: describe the mess you're actually in, then say
what you want back — a recommendation, the trade-offs, a decision framework, whatever helps.
Two examples to steal from:

**1. CI identification & deduplication strategy**

> We're loading CMDB from three places — Discovery, a Service Graph Connector, and a legacy
> import that predates all of us — and we're drowning in duplicate CIs. Can you help me work
> out how identification _should_ be settled here?
>
> The parts I keep going back and forth on: when to trust Discovery's identification rules
> versus the identifiers a connector hands us versus writing our own IRE rules, and how
> datasource precedence is supposed to resolve it when two sources claim the same attribute.
> I'm also never sure where the line falls between dependent and independent CI
> identification.
>
> Reason it through with me rather than jumping to an answer, then land on a default
> authoritative-source model and name the exceptions where it shouldn't apply. If you can
> point at the docs behind the big calls, that'd help me sell this internally.

**2. Multi-instance platform & domain strategy at scale**

> I'm advising a global enterprise — 12 business units, a bit over 200k employees — and we
> have to settle the platform topology before anything else can move. Single instance with
> domain separation? Separate production instances? Something hub-and-spoke? I'd honestly
> rather see the trade-offs laid out than be handed a verdict.
>
> Two things are fixed: each BU needs its own isolation, but they share a CMDB. And please
> stick to GA features — I can't build a plan on what's coming next year.
>
> Once you've picked a direction, I'll need the rest of the story: how we handle archiving
> and table rotation on the high-volume tables, what the cross-instance integration pattern
> looks like, which performance levers are actually worth pulling, and what any of this does
> to our licensing.
>
> Give me the short recommendation first, then the detail behind it. Close with the three
> architectural risks you'd lose sleep over.

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
| Baseline download (transient — deletable)        | ~1.0 GB |

Figure a little extra headroom during the first download — the compressed baseline and the
restored copy coexist until you clear `~/.local/share/embeddington/work/` (or
`$EMBEDDINGTON_HOME/work/` if set) — plus **~6–8 GB RAM** — the embedder alone holds
~2.3 GB once bge-m3 loads, on top of Qdrant + ArangoDB serving the full graph.

Upgrading? Downloads used to land in `data/work/` inside your clone. That directory is no
longer used and can be deleted outright — it may still be holding ~1 GB of baseline scratch.

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
  cursor only advances after a diff fully applies — so an interrupted diff-apply run resumes
  cleanly at the next diff. An interrupted baseline download restarts that one asset from
  zero (it streams to disk, so it won't eat your RAM doing it). This aggression toward data
  loss will not stand.

## Configuration

`embeddington-setup` flags (all optional):

| Flag                   | Purpose                                                                    |
| ---------------------- | -------------------------------------------------------------------------- |
| `--check`              | Doctor mode: report health, change nothing, exit 0 (healthy) or 1          |
| `--uninstall`          | Interactively remove embeddington, asking about each owned item separately |
| `--yes`                | Unattended: defaults everywhere, no prompts                                |
| `--really-delete-data` | With `--yes`: allow unattended deletion of data volumes/clone              |
| `--force-baseline`     | Forwarded to the updater: re-restore the full baseline                     |

`install.sh` environment variables (all optional):

| Variable                   | Default                                          | Purpose                                                                        |
| -------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------ |
| `EMBEDDINGTON_YES`         | unset                                            | `1` for a fully unattended install (never installs Docker, never deletes data) |
| `EMBEDDINGTON_INSTALL_DIR` | `~/embeddington`                                 | Where to clone and install                                                     |
| `EMBEDDINGTON_CLONE_URL`   | `https://github.com/whiffernet/embeddington.git` | Clone source override (CI, forks)                                              |

`embeddington-consume update` flags (all optional — `--repo` defaults to
`whiffernet/embeddington`; override it only if you've forked):

| Flag                | Default                   | Purpose                                                      |
| ------------------- | ------------------------- | ------------------------------------------------------------ |
| `--repo`            | `whiffernet/embeddington` | `owner/name` of this releases repo                           |
| `--cursor`          | `<state dir>/.cursor`     | Local cursor file                                            |
| `--work-dir`        | `<state dir>/work`        | Scratch dir for downloads                                    |
| `--force-baseline`  | off                       | Ignore the cursor and re-restore the full baseline (~828 MB) |
| `--qdrant-url`      | `http://localhost:6333`   | Local Qdrant                                                 |
| `--collection`      | `technology`              | Qdrant collection name                                       |
| `--arango-url`      | `http://localhost:8529`   | Local ArangoDB                                               |
| `--arango-db`       | `technology_kg`           | Target database                                              |
| `--arango-user`     | `root`                    | ArangoDB user                                                |
| `--arango-password` | `$ARANGO_ROOT_PASSWORD`   | ArangoDB password                                            |

The **state directory** holds the cursor — the record of which version of the graph you have.
It resolves in this order: `$EMBEDDINGTON_HOME`, then `$XDG_DATA_HOME/embeddington`, then
`~/.local/share/embeddington`. There is one local stack per machine, so there is one cursor
per machine — which is why the working directory no longer matters.

> **Careful with `$EMBEDDINGTON_HOME` / `$XDG_DATA_HOME`.** If you export either one from
> `.bashrc` (or a login profile), **cron does not inherit it** — cron starts a bare shell.
> The cron run then looks for the cursor in `~/.local/share/embeddington/`, doesn't find the
> one your interactive shell has been maintaining, and stops with the "already has N points"
> refusal (exit 3). Either set the variable inside the crontab line itself, or don't set it
> at all.

Upgrading from a version that kept its cursor in `data/.cursor`? The first run adopts it and
says so — nothing is re-downloaded — **provided that old cursor is somewhere the CLI looks**:
the current directory, the install root (your clone, for the documented `pip install -e .`),
or `$HOME`. If you once ran the tool from a fourth place (say `~/work`), copy that file into
the state directory yourself before the first run:

```bash
mkdir -p ~/.local/share/embeddington
cp ~/work/data/.cursor ~/.local/share/embeddington/.cursor
embeddington-consume update
```

(Use `$EMBEDDINGTON_HOME` or `$XDG_DATA_HOME/embeddington` instead if you've set either.)
A copy — not `--cursor ~/work/data/.cursor`: that flag only tells this one run where the
cursor file lives, and keeps using it in place. It's a permanent flag, not a migration.

Every old cursor the first run _does_ find is renamed to `data/.cursor.migrated` (kept, not
deleted) — all of them, not just the one it adopts, so none can be mistaken for a live cursor
later.

Two more upgrade housekeeping notes:

- Your old scratch dir — `data/work/` in the clone — is orphaned now that downloads land in
  `<state dir>/work/`. It can hold up to ~1 GB of baseline leftovers; delete it.
- **Exit codes**, for anyone wrapping this in a job runner:

  | Code | Meaning                                                                                                                                                                                                                                                 |
  | ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | `0`  | Success (restored, applied diffs, or already up to date)                                                                                                                                                                                                |
  | `1`  | Unhandled error                                                                                                                                                                                                                                         |
  | `3`  | **Refused**: a baseline was needed, but the stores already hold data and no cursor was found. Nothing was downloaded. Copy your old cursor into the state dir (above) and re-run, or pass `--force-baseline` if you really want the ~828 MB re-restore. |

  (There is no `2`: it's reserved for `BaselineRequired`, which only the library can raise —
  the CLI always supplies a baseline importer.)

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

## When the plan comes apart (troubleshooting)

> _"This is a very complicated case."_

Every installer failure prints an `[EMB-nn]` code with a fix line already attached. Find
yours here for the full story.

#### EMB-10 — no interactive terminal

`install.sh` was piped without a TTY and `EMBEDDINGTON_YES` isn't set — it can't prompt
for anything. Run it from a real terminal, or set `EMBEDDINGTON_YES=1` for an
unattended install.

#### EMB-11 — git missing

`git` isn't on `PATH`. Install it (`xcode-select --install` on macOS; `apt`/`dnf
install git` on Linux), then re-run.

#### EMB-12 — python too old or missing

No `python3.13`, `python3.12`, or `python3` on `PATH` resolves to 3.12+. Install
Python 3.12 or newer (python.org, `brew install python@3.12`, or your distro), then
re-run.

#### EMB-13 — can't reach the repo

`git ls-remote` against the clone URL failed — no network, or a proxy is in the way.
Check your connection, then re-run.

#### EMB-14 — venv/pip bootstrap failed

Three distinct causes share this code, and `install.sh` tells you which: the
`python3-venv` package is missing (`sudo apt install python3-venv`, or
`python3.12-venv`, then re-run); a `pip install` step failed (the last 20 lines of
`install.log` print above the error — fix what it complains about, then re-run); or
the clone is stale and `embeddington-setup` never landed (`cd` into the install dir,
`git stash && git pull --ff-only`, then re-run).

#### EMB-15 — not enough disk

Preflight found less than 3 GB free. Free up at least 3 GB (12+ recommended), then
re-run.

#### EMB-16 — install dir isn't empty and isn't a clone

The install directory exists, has files in it, and isn't an embeddington git clone —
`install.sh` won't overwrite something it doesn't recognize. Pick a different location
(`EMBEDDINGTON_INSTALL_DIR=...`), or move that directory aside.

#### EMB-20 — docker install declined

No container runtime was found and every offer to install one was turned down (or,
in `--yes` mode, there was no one to ask — unattended mode never installs Docker
because it can't consent on your behalf). Install OrbStack, Colima, Docker Desktop,
or Docker Engine yourself, then re-run — or run interactively without
`EMBEDDINGTON_YES` so the wizard can offer.

#### EMB-21 — docker daemon not reachable

The daemon didn't come up within the wait window, or it's up but your user can't
reach its socket yet (fresh Linux installs aren't in the `docker` group by default —
the wizard offers `usermod -aG docker`, but that only takes effect after you log out
and back in, or run `newgrp docker`). Start the daemon manually (OrbStack/Docker
Desktop, `colima start`, or `sudo systemctl start docker`) or re-login, then re-run.

#### EMB-22 — manual runtime install required

The wizard can't finish this install path for you — no Homebrew to install OrbStack
with, an OrbStack brew install that failed, Colima's three-step manual setup, or
Docker Desktop (a GUI download it can't script). Follow the printed steps or install
a runtime yourself, then re-run.

#### EMB-23 — automatic docker install failed or unsupported

Either the `docker compose` v2 plugin is missing after an otherwise-working Docker
install, the Linux distro wasn't recognized so the wizard wouldn't guess a package
manager, or the recognized distro's package install command failed. Install Docker
Engine + the compose plugin per
[docs.docker.com/engine/install](https://docs.docker.com/engine/install/), then
re-run.

#### EMB-24 — port already taken

A port `consumer/docker-compose.yml` needs is bound by something that isn't
embeddington. Stop whatever holds that port (or move it), then re-run.

#### EMB-31 — docker compose up failed

Either `docker compose up -d --build` exited non-zero (the error prints just above),
or Qdrant/ArangoDB didn't answer within the store timeout. Fix what compose
complained about (ports, disk, daemon) — or check `docker compose ps` and
`docker compose logs` in `consumer/` — then re-run; it picks up where it left off.

#### EMB-32 — embed service didn't come up

The `embed` service's first build downloads ~2 GB of model weights, and that stalled
or failed past the embed timeout. Run `docker compose logs embed` in `consumer/` to
see why; a plain retry (`docker compose up -d --build`) resumes a dropped download
cleanly.

#### EMB-33 — no usable ArangoDB password

`consumer/.env` either doesn't exist, or exists but its `ARANGO_ROOT_PASSWORD` is
empty or still the placeholder `change-me`. Re-run the installer to generate one, or
open the file and set `ARANGO_ROOT_PASSWORD` to any non-empty value yourself.

#### EMB-41 — download failed (network)

A baseline or diff download hit a network error. Check your connection and re-run —
downloads resume/retry cleanly.

#### EMB-42 — asset checksum mismatch

A downloaded asset failed checksum verification. Re-run — a corrupted download
re-fetches cleanly. If it repeats, open an issue.

#### EMB-43 — populated store with no cursor

The stores already hold data and no cursor was found, so the updater refuses to
guess whether a full re-restore is safe (this is the same guard `embeddington-consume
update` exits `3` for). If the store is healthy, copy your old cursor into the state
dir (see **Configuration** above); to deliberately re-restore everything, re-run with
`--force-baseline`.

#### EMB-44 — proof-of-life query returned zero

After import, a real query against Qdrant and ArangoDB found at least one store
empty or unqueryable. Give the containers a few seconds to settle and re-run
`embeddington-setup --check`; if it persists, check `docker compose logs` in
`consumer/`, or run `embeddington-consume update --force-baseline` for a clean
restore.

#### EMB-45 — updater error

The updater hit something other than a network, checksum, or guard failure (a chain
gap, a schema version mismatch, ...). Re-run the installer; if it repeats, run
`embeddington-consume update` directly for the full error.

#### EMB-51 — MCP dependency install failed

`pip install -r mcp/requirements.txt` failed while wiring up Claude — the graph
itself is unaffected and fully usable without it. Run that `pip install` manually to
see why, then launch Claude from the repo root with `consumer/.env` loaded.

#### EMB-61 — couldn't inspect store contents before deletion

Uninstall couldn't query the stores (daemon down?) before offering to delete their
volumes, so it can't prove they hold only embeddington data. This is a non-fatal
warning, not a stopper. For an inspected deletion: `cd consumer && docker compose up
-d`, then re-run the uninstall — or proceed knowing the contents are unverified.

#### EMB-62 — crontab rewrite failed

Uninstall couldn't rewrite your crontab to strip the embeddington line. Run
`crontab -e` and remove the line yourself.

#### EMB-63 — clone self-delete handoff failed

Uninstall hands off to a tiny detached script to delete the clone (so the running
Python process isn't deleting the directory it's executing from); the handoff
`execv` itself failed. Remove the clone yourself: `rm -rf <clone path>`.

---

## Who's got the papers (license & data provenance)

> _"Is this your homework, Larry?"_

The **code** in this repository — the consumer CLI and the bundled MCP server — is licensed
under the **Apache License 2.0**. See [`LICENSE`](LICENSE).

The **data** is derived, not original. Both the vectors and the graph are extracted from
**[ServiceNow/ServiceNowDocs](https://github.com/ServiceNow/ServiceNowDocs)** — ServiceNow's
own platform documentation, © ServiceNow, published under the Apache License 2.0. The
derived artifacts shipped here (Qdrant chunk embeddings, `entities_v2`, `relationships_v2`)
are redistributed under those same terms.

Nothing in this graph is authoritative on its own. Every relationship carries the
`source_document` it came from and the `source_quote` that produced it, precisely so a claim
can be checked against the sentence that produced it. When the graph and the docs disagree,
**the docs are right** — extraction is lossy, and an edge is a compression of a sentence.

This project is not affiliated with, endorsed by, or supported by ServiceNow.

---

## Careful, man (third-party components)

> _"Careful, man, there's a beverage here!"_

The `LICENSE` at the root of this repo covers **embeddington's own code** — the consumer CLI
and the bundled MCP server — under Apache 2.0. It does **not** cover the databases
embeddington talks to, and one of them has terms you'll want to know about before you go
building a business on it.

Nothing here ships you a database. `consumer/docker-compose.yml` names two images; your
Docker pulls them from their vendors, and you accept their terms directly from them. What
embeddington distributes is **data** — a Qdrant snapshot, an ArangoDB dump, and daily diffs.
No engine source, no binaries, no images.

| Component           | Pinned version     | License      | The short of it                                                            |
| ------------------- | ------------------ | ------------ | -------------------------------------------------------------------------- |
| **Qdrant**          | `v1.16.3`          | Apache 2.0   | No strings. Use it, ship it, sell it.                                      |
| **ArangoDB**        | `3.12.4`           | **BUSL 1.1** | Not an open-source license. Read the next bit.                             |
| **BAAI/bge-m3**     | —                  | MIT          | Weights download from Hugging Face on first run; nothing is redistributed. |
| **ServiceNow docs** | branch `australia` | Apache 2.0   | The source of truth. See the provenance section above.                     |

### The ArangoDB bit

ArangoDB moved to the **Business Source License 1.1** in the 3.12 line. Its own text is
refreshingly blunt:

> The Business Source License … is not an Open Source license.

What it grants you, verbatim:

> you may make use of the Licensed Work internally in production, provided that you may not
> use the Licensed Work in a commercial offering that allows one or more third parties
> (other than your contractors) to access, create or manage databases including data that is
> controlled by any such third parties.

In the parlance of our times:

- **Running embeddington on your own machine, or inside your own company?** That's the whole
  point. Go nuts.
- **Selling a hosted service where your customers' data lives in that ArangoDB?** That's the
  thing it says no to. You'd need a commercial license from ArangoDB.
- **Waiting it out?** BUSL converts to Apache 2.0 on its Change Date — the fourth
  anniversary of the March 2024 release, so roughly **March 2028** for the 3.12 line. The
  clock is per-version: upgrade the engine, restart the clock.

This obligation runs between **you and ArangoDB**, not between you and this repo.
embeddington hands you a compose file, not a database.

### Not a lawyer, man

> _"That's just, like, your opinion, man."_

This section is a summary written in good faith, not legal advice. The licenses themselves
are the authority: [Qdrant](https://github.com/qdrant/qdrant/blob/master/LICENSE),
[ArangoDB](https://github.com/arangodb/arangodb/blob/devel/LICENSE). If real money is riding
on that DBaaS clause, spend ten minutes with someone who does this for a living.

---

<p align="center"><em>The graph abides.</em></p>
