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
- **ArangoDB** — an entity/relationship graph (`entities_v3` / `relationships_v3`) for
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

Because there are three ways to ask a question and they fail differently. The docs are the
truth, one page at a time — brutal when the answer isn't written on any single page, and
across ~48,000 markdown files most interesting answers aren't. Vector search finds passages
that _resemble_ your question, which is the wrong tool when **no passage contains the
answer** — when it only exists in the relationships _between_ passages. A graph does that
joining ahead of time: extraction reads every page once and writes the relationships down as
typed edges, so a multi-hop question is a traversal instead of an afternoon.

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

These are the counts of the current published baseline, **`baseline-2026-09c`** — and, with
no diffs published on top of it yet, exactly what a fresh install restores today. They move
when the next baseline or diff batch is published.

| Metric                                      | Count       |
| ------------------------------------------- | ----------- |
| Vectors (Qdrant chunks, `bge-m3`, 1024-dim) | **70,699**  |
| Entities (graph nodes)                      | **271,274** |
| Relationships / triples (graph edges)       | **561,618** |
| Entity types                                | 14          |
| Relationship predicates                     | 14          |
| Avg. relationships per entity               | ~2.1        |

Each edge is one subject–predicate–object triple, so "relationships" and "triples" are the
same count. Distance metric is cosine; chunking is ~1500 tokens / 200 overlap.

The entity and edge counts are lower than the previous baseline's, which reported 355,523
and 809,806. This is the first baseline on **KG schema v3**, and v3 re-derives the graph
from the corpus rather than migrating the old rows forward. The CHANGELOG has the details.

> _"I'm the Dude. So that's what you call me."_ — one document, one id, every time.

Every chunk is **one current version of one document** — no superseded copies, so a search
never hands you back two versions of the same page. Chunk ids are derived from the document
path and release, so when a document changes, its new chunks take the old ones' place instead
of piling up beside them. One rug, not a stack of them.

---

## Before you roll (prerequisites)

> _"This is not Docs. This is embeddington. There are rules."_

- **Python 3.12+** — the one hard prerequisite. The installer is written in Python and
  bails early (with a clear message) if it's missing.
- **Docker** (with the Compose plugin) — runs the local Qdrant + ArangoDB + embedder. You
  don't have to install it yourself first: if it's absent, the one-liner installer detects
  that and offers to set it up for you (OrbStack/Colima on macOS, apt/dnf on Linux), all
  consent-gated. So you can start from the one command either way.
  **Already have one?** It's found first — before any install is offered — even when it
  isn't on your `PATH`. OrbStack in particular puts its CLI in `~/.orbstack/bin` and gets
  it onto `PATH` through a shell-init edit, which a non-login shell (a VS Code integrated
  terminal, say) never reads. The wizard checks the usual homes, uses whatever it finds
  for that run, and if yours lives somewhere unusual it asks rather than guessing —
  `EMBEDDINGTON_DOCKER_BIN=/path/to/docker` skips the question entirely.

No account, no token, no access request — the download is a plain HTTPS GET.

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

- Re-running it later is safe, from anywhere: the installer remembers where it put things
  and offers **Update / Repair / Uninstall** on a machine that already has it. You do not
  need to remember the path or pass it again — just re-run the same one command.
- **macOS:** if you point it at `~/Documents`, `~/Desktop`, `~/Downloads`, or iCloud Drive,
  it says so and offers `~/embeddington` instead. Background jobs cannot read those folders
  without a Full Disk Access grant, so a clone there never auto-updates.
- Unattended (CI, scripts): `EMBEDDINGTON_YES=1`, install dir via
  `EMBEDDINGTON_INSTALL_DIR`. Unattended mode never installs Docker (it can't consent)
  and never deletes data.
- Prefer to read before you pipe? [`install.sh`](install.sh) is a hundred-odd boring lines; the
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

## Roll it forward (staying current)

> _"New information has come to light, man."_

Your graph already landed during install — the wizard restored the baseline and verified it,
so there's nothing to import by hand. This section is about **keeping it current** afterward.

Re-running the one-liner on a box that already has embeddington opens a short menu:

- **Update** — the routine path. Pulls the latest, brings your local stack up to the
  current config (re-syncing dependencies only if they changed), applies data diffs, and
  keeps the keyword search index complete. Everything it does is safe to run as often as
  you like; when nothing changed, it's a quick no-op. Your data stays live throughout.
- **Repair** — the bigger hammer. Use it when search returns nothing or a container
  won't start: it re-verifies every step and rebuilds whatever's broken. If the embedder
  image needs rebuilding that can take 10–20 minutes; if everything's actually healthy
  it finishes fast.

After a code update your data works immediately; to load new Claude search-tool code,
reopen Claude Desktop (Claude Code picks it up on its next run).

**The installer offers to schedule it.** During install (and on **Repair**) the wizard asks
_"Set up daily auto-updates at 06:00?"_ — say yes and it writes the crontab entry
idempotently (`embeddington-setup --uninstall` removes it). **The wizard's receipt prints the
line for your own install**, with the right path already filled in from where it found
docker; copy it from there. To write one by hand:

```bash
# crontab -e   — update daily at 06:00
0 6 * * * PATH=/usr/local/bin:$PATH; cd $HOME/embeddington && set -a && . consumer/.env && set +a && .venv/bin/embeddington-setup --yes >> $HOME/embeddington-update.log 2>&1
```

It runs the same unattended **Update**, so a box that rebooted or had Docker die overnight
self-heals at 06:00 rather than sitting there stale. The `cd` is what makes the relative
`consumer/.env` resolve, which is where `ARANGO_ROOT_PASSWORD` comes from.

**That `PATH=` is not optional.** cron runs with roughly `/usr/bin:/bin`, and on macOS every
container runtime lives outside it — Docker Desktop in `/usr/local/bin`, OrbStack in
`~/.orbstack/bin`, Colima via Homebrew in `/opt/homebrew/bin`. Without it the nightly job
finds no `docker` and fails every night into a log nobody reads. `dirname "$(command -v
docker)"` prints yours. Linux mostly gets away without it, since `/usr/bin/docker` is
already on cron's path.

**How to tell whether any of that is actually happening.** Every way a scheduled update can
fail is silent — the cron daemon isn't running, macOS won't let a background job read the
folder, the laptop was asleep at 06:00 (cron skips; it does not catch up), the WSL2 distro
was shut down. So the install records each successful run, and tells you when they stop:

```bash
embeddington-setup --check
```

The `updates` row reads `last successful run 3d ago (v0.14.2)` on a healthy machine and says
how far behind you are when it isn't, along with the release you're on. An install that has
gone more than a month without updating also gets mentioned once through Claude — someone
whose updates stopped is by definition not the person running the installer. Nothing is
reported anywhere: the record is a local file, read locally, and removed by
`embeddington-setup --uninstall`.

If you'd rather run the data-only piece by hand for some reason — diffs and the keyword
index, no container/config/venv changes — `embeddington-consume update` is still there; see
**Configuration** below for its flags.

<details><summary>Auto-updates on macOS and WSL2 (platform notes)</summary>

The daily cron job is a plain crontab entry. On a normal Linux box with cron running it
just works. Each platform has a wrinkle worth knowing:

- **Linux:** if the wizard warned that no cron daemon was detected, start it —
  `sudo service cron start` (or `sudo systemctl enable --now cron`), then the 06:00 job
  fires.
- **macOS:** cron starts itself the first time a crontab exists, so the "no cron daemon
  detected" warning right after enabling is usually just a timing blip — the job will
  still run. Two real caveats: (1) if `crontab` fails to write, grant your terminal app
  **Full Disk Access** (System Settings → Privacy & Security) and re-run; (2) cron jobs
  inherit macOS privacy limits, so if you installed embeddington **under `~/Documents`,
  `~/Desktop`, `~/Downloads`, or iCloud Drive**, the nightly `cd` into it can be blocked
  and the update silently does nothing — install somewhere like `~/embeddington` or
  `~/code/embeddington` to avoid this. The installer now warns before cloning into one of
  those folders, and warns again on every run if you are already installed in one; if that
  is you, the fix is to move the clone (or grant Full Disk Access to the program that runs
  the update) — until then, treat updates as something you run by hand.
- **WSL2:** a crontab entry only fires while the distro is running, and WSL2 shuts the
  distro down when idle and does not launch it at boot. For reliable 06:00 updates you
  need `systemd=true` in `/etc/wsl.conf` **and** something keeping the distro alive (e.g.
  a Windows Task Scheduler job that runs `wsl.exe`). Without that, prefer running
  `embeddington-setup` (Update) by hand when you want fresh data.

**Arango memory:** the local database sizes its caches from a memory cap (default 4 GB,
or half your RAM on smaller machines — the installer picks a safe value automatically and
writes it to `consumer/.env` as `ARANGO_MEMORY_CAP`). On a very small host you can lower
it by hand (e.g. `ARANGO_MEMORY_CAP=1G`) and re-run Update. If the database won't stay up
after an update on a low-RAM box, that's the first knob to turn.

</details>

Most runs are tiny — just the newer diffs. The exception is a **re-baseline**: after the
publisher compacts history, the next update does a full restore — it **drops and recreates**
the local Qdrant collection from the baseline's manifest config, streams every point back in,
then restores the Arango dump on top (several minutes; the same few-hundred-MB download as any
other baseline). That's expected, not an error.

> _"This aggression will not stand, man."_ — your old chunks, overruled. A baseline is a
> complete statement of the corpus, not a suggestion.

A baseline restore **replaces** your local collection rather than merging into it — anything
already stored is cleared first. Merging would leave the previous generation sitting alongside
the new one, two copies of every document, and nobody wants that in their rug.

What it prints. **First run** (or the first run after a new baseline is cut) restores the
whole graph:

```
Embeddington update complete.
  Action:  restored full baseline (baseline-2026-09c)
  Loaded:  70,699 vectors · 271,274 entities · 561,618 edges
  Version: ts-161fc74e847211ad
  Diffs:   0 applied on top of the baseline
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

A baseline restore reporting `Diffs: 0` is a **success**, not a no-op — it means the baseline
it just loaded was already current. Nothing more to fetch, man.

---

## Leaving town (uninstall)

> _"Sometimes there's a man... sometimes, there's a man."_

Same command, or `embeddington-setup --uninstall` from the clone. It shows everything
embeddington owns (containers, volumes, state dir, cron line, the Claude MCP registration,
the clone), asks about each item separately — every default is **No** — and looks _inside_ the stores first:
if it finds collections or databases you created, it names them and refuses to offer
volume deletion until you acknowledge. The knowledge-graph volumes require typing
`delete`; a plain `y` won't do it. Shared infrastructure (Docker, OrbStack, Colima,
Homebrew) is never removed — the receipt lists the manual commands if you want them
gone too.

---

## In the parlance of our times (query with Claude)

> _"You're entering a world of context."_

`mcp/` is a stdio MCP server exposing vector search and graph traversal over your local
stores. It shows up in Claude Code under **`/mcp`** as **embeddington** (its tools as
`mcp__embeddington__…`). It is not a plugin and will never appear under `/plugin` — if
that's where you went looking, nothing is broken.

**The installer wires it for you.** It points the config at the clone's own interpreter,
then starts the server once to prove it works rather than assuming it does. The password
needs no wiring at all: the server reads `ARANGO_ROOT_PASSWORD` straight from
`consumer/.env`, so the credential stays in the one 0600 file that already held it rather
than being copied into a second one. Nothing depends on the environment you launch from —
no exported password, no activated venv:

```bash
# run from: repo root
claude
```

Approve the `embeddington` server when prompted, then `/mcp` to confirm it connected.

**Want it everywhere, not just here?** The shipped `.mcp.json` is *project-scoped*: it
exists only when the clone is your project directory. During install the wizard offers a
user-scope registration named **`embeddington-local`**, which works from any directory.
To add it later by hand:

```bash
claude mcp add embeddington-local --scope user -- "$PWD/.venv/bin/python" "$PWD/mcp/server.py"
```

(Absolute paths are not optional there — a relative command under user scope is never
started at all from another directory.) `embeddington-setup --uninstall` removes the
registration along with everything else.

### Slash commands

Launch Claude Code from the clone and nine commands come with it — thin wrappers over the
MCP tools that carry the caveats you would otherwise have to remember:

| Command | What it does |
| --- | --- |
| `/embeddington-ask <question>` | The main path. Answers from the graph and refuses to fill gaps from memory when the retrieval came back thin. |
| `/embeddington-search <terms>` | Raw semantic hits with scores — the evidence, not a synthesis. |
| `/embeddington-find <name>` | What something is actually called in here, with the ids the other commands need. |
| `/embeddington-entity <name>` | Resolves a name to an entity and shows what it connects to. |
| `/embeddington-neighbors <name>` | Deeper exploration, with depth and predicate filters chosen deliberately. |
| `/embeddington-path <a> <b>` | How two things connect, with the hub caveat applied (see below). |
| `/embeddington-schema` | The entity types and predicate vocabulary — what you can actually ask for. |
| `/embeddington-doctor` | Runs the health check and translates the rows. |
| `/embeddington-update` | Runs the unattended update and reads the receipt back. |

Three of these encode things that are easy to get wrong. `/embeddington-ask` carries
`enrich`'s do-not-fabricate contract: when the `grounding` signal says the retrieval was
weak or empty, it tells you what was *not* found instead of answering from prior knowledge.
`/embeddington-path` applies the measured hub caveat — most paths between arbitrary
entities in this graph route through a handful of very popular nodes, and such a path means
"both of these touch something popular", not "these two are related". And
`/embeddington-neighbors` insists on `/embeddington-schema` before filtering by predicate,
because predicates are a controlled vocabulary and a guessed one returns nothing while
looking exactly like an empty neighbourhood.

Unlike the MCP server, these are **project-scoped only**: they live in the clone's
`.claude/commands/` and are available when the clone is your project directory. Copy them
into `~/.claude/commands/` if you want them everywhere.

### Pointing it somewhere else

Pointing the server at a different store, a scoped read-only user, or a Qdrant that needs an
API key is what `mcp/.env` is for: `cp mcp/.env.example mcp/.env` and set
what you need. It takes precedence over `consumer/.env`, and an explicit environment
variable takes precedence over both. Claude Desktop needs no extra wiring either: point it
at `mcp/server.py` (see `mcp/README.md`) and the same resolution applies, which matters
because a GUI app inherits none of your shell's exports.

> _"This is a private residence, man."_ `.mcp.json` connects as `ARANGO_USER: root`. That's
> **your own** ArangoDB container — the one `consumer/docker-compose.yml` started, with the
> password you chose in `consumer/.env`. No shared credential ships with this repo, and
> nothing here reaches a database you don't own.

**Pointing the server at your own remote Arango instead?** `root` is fine against the local
consumer stack above — it's your container, on loopback. Point `ARANGO_URL` at a non-loopback
host with `ARANGO_USER` still `root`, though, and the server refuses to start: use the scoped
read-only user (`kg_servicenow_ro`) for a remote or production store instead. That refusal
exists to stop a BYO-prod Arango from booting under full-admin creds by accident; if you've
deliberately decided to accept the risk, set `EMBEDDINGTON_ALLOW_REMOTE_ROOT=1`. See
**`mcp/README.md`** for the full variable table and the exact `SystemExit` text.

```bash
# run from: repo root — the installer does this; here it is by hand
.venv/bin/pip install -r mcp/requirements.txt
```

Use the clone's own `.venv/bin/pip`, not a bare `pip`: the server is launched with
`.venv/bin/python`, and dependencies installed anywhere else are invisible to it. If the
server won't start, `embeddington-setup --check` names the reason, and **EMB-52** in
[docs/troubleshooting.md](docs/troubleshooting.md) walks through it.

Both query styles work out of the box: graph traversal (`kg_find_entities`, `kg_neighbors`,
`kg_path`, `kg_schema`, `kg_get_entity`) runs against your local ArangoDB, and
`vector_search` / `enrich` use the local `embed` service — the same `bge-m3` model the
collection was built with, so a query lands in the exact vector space of the data. No
outside embedding API. The `.mcp.json` already points `EMBED_URL` at it.

Vector retrieval is hybrid: a dense (cosine) lane is fused with a lexical lane for
identifier-style tokens (`cmdb_rel_ci`, `com.snc.discovery`), so table/field/plugin names
hit their exact chunks instead of getting buried in prose matches. Weak dense matches are
dropped rather than padded in, so `vector_search` and `enrich` can honestly return fewer
results than you asked for when nothing clears the relevance floor — see `mcp/README.md`
and `mcp/RESPONSE_SHAPES.md` for the details.

`enrich` also tells you when it didn't actually find what you asked about: a `grounding`
signal on the response distinguishes a solid answer from one that's thin or empty, so
Claude can say what wasn't found instead of guessing — see `mcp/RESPONSE_SHAPES.md`.

---

## Take 'er for a spin (example prompts)

> _"This is a very complicated case. A lotta ins, a lotta outs, a lotta what-have-yous."_

With the embeddington MCP loaded, ask Claude the kind of deep, multi-hop ServiceNow
architecture questions that need the graph **and** the docs together. Talk to it like you'd
talk to a colleague who's read everything: describe the mess you're actually in, then say
what you want back — a recommendation, the trade-offs, a decision framework, whatever helps.

From inside the clone you can put **`/embeddington-ask`** in front of any of these and get the
same answer with the grounding contract applied: if the graph doesn't actually hold what you
asked about, it says so instead of filling the gap from memory. See
[Slash commands](#slash-commands) for the other eight.

Two examples to steal from:

**1. CI identification & deduplication strategy**

> /embeddington-ask We're loading CMDB from three places — Discovery, a Service Graph
> Connector, and a legacy import — and we're drowning in duplicate CIs. When should we trust
> Discovery's identification rules versus the identifiers a connector hands us versus our own
> IRE rules, and how is datasource precedence supposed to resolve it when two sources claim
> the same attribute? Where does the line fall between dependent and independent CI
> identification?
>
> Reason it through rather than jumping to an answer, then land on a default
> authoritative-source model and name the exceptions. Point at the docs behind the big calls.

**2. Multi-instance platform & domain strategy at scale**

> I'm advising a global enterprise — 12 business units, 200k+ employees — and we have to
> settle the platform topology first. Single instance with domain separation? Separate
> production instances? Hub-and-spoke? Each BU needs isolation but they share a CMDB, and
> stick to GA features. Lay out the trade-offs rather than handing me a verdict, then close
> with the three architectural risks you'd lose sleep over.

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
| Baseline download (transient — cleaned up for you) | ~1.0 GB |

Figure a little extra headroom during the first download — the compressed baseline and the
restored copy coexist while the restore runs, in `~/.local/share/embeddington/work/` (or
`$EMBEDDINGTON_HOME/work/` if set). That scratch is deleted once the restore succeeds, and
each nightly diff bundle is deleted once it applies, so the directory does not grow over
time. A failed download, or a diff that could not be applied, is left in place deliberately —
it is evidence, and re-running re-fetches it anyway. Plus **~6–8 GB RAM** — the embedder
alone holds ~2.3 GB once bge-m3 loads, on top of Qdrant + ArangoDB serving the full graph.

These figures track the current baseline and move with it.

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
- A full baseline restore also warms the MCP server's lexical search index (`chunk_text`)
  as part of the import, so hybrid retrieval is ready the moment the restore finishes rather
  than on your first query. It prints its own status line (`chunk_text index: ready`); if
  you ever restore a Qdrant snapshot by hand outside `embeddington-consume`, re-run it
  standalone with `embeddington-consume ensure-index`.

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
| `EMBEDDINGTON_INSTALL_DIR` | remembered install, else `~/embeddington`         | Where to clone and install. Overrides the remembered location; skips the prompt |
| `EMBEDDINGTON_CLONE_URL`   | `https://github.com/whiffernet/embeddington.git` | Clone source override (CI, forks)                                              |
| `EMBEDDINGTON_DOCKER_BIN`  | unset                                            | Full path to your `docker` CLI, for a runtime installed somewhere the wizard doesn't look                    |

`embeddington-consume update` flags (all optional — `--repo` defaults to
`whiffernet/embeddington`; override it only if you've forked):

| Flag                | Default                   | Purpose                                                      |
| ------------------- | ------------------------- | ------------------------------------------------------------ |
| `--repo`            | `whiffernet/embeddington` | `owner/name` of this releases repo                           |
| `--cursor`          | `<state dir>/.cursor`     | Local cursor file                                            |
| `--work-dir`        | `<state dir>/work`        | Scratch dir for downloads                                    |
| `--force-baseline`  | off                       | Ignore the cursor and re-restore the full baseline (~1 GB) |
| `--qdrant-url`      | `http://localhost:6333`   | Local Qdrant                                                 |
| `--collection`      | `technology`              | Qdrant collection name                                       |
| `--arango-url`      | `http://localhost:8529`   | Local ArangoDB                                               |
| `--arango-db`       | `technology_kg`           | Target database                                              |
| `--arango-user`     | `root`                    | ArangoDB user                                                |
| `--arango-password` | `$ARANGO_ROOT_PASSWORD`   | ArangoDB password                                            |

The **state directory** holds the cursor — the record of which version of the graph you have.
It resolves in this order: `$EMBEDDINGTON_HOME`, then `$XDG_DATA_HOME/embeddington`, then
`~/.local/share/embeddington`. There is one local stack per machine, so there is one cursor
per machine — which is why the working directory does not matter.

> **Careful with `$EMBEDDINGTON_HOME` / `$XDG_DATA_HOME`.** If you export either one from
> `.bashrc` (or a login profile), **cron does not inherit it** — cron starts a bare shell.
> The cron run then looks for the cursor in `~/.local/share/embeddington/`, doesn't find the
> one your interactive shell has been maintaining, and stops with the "already has N points"
> refusal (exit 3). Either set the variable inside the crontab line itself, or don't set it
> at all.

**Exit codes**, for anyone wrapping this in a job runner:

| Code | Meaning |
| ---- | ------- |
| `0`  | Success (restored, applied diffs, or already up to date) |
| `1`  | Unhandled error |
| `3`  | **Refused**: a baseline was needed, but the stores already hold data and no cursor was found. Nothing was downloaded. Pass `--force-baseline` if you want the ~1 GB re-restore. |

(There is no `2`: it's reserved for `BaselineRequired`, which only the library can raise —
the CLI always supplies a baseline importer.)

> _"This is what happens when you float your version tags."_
>
> The `docker-compose.yml` pins Qdrant to the exact version the baseline was built and
> tested against — collection config (HNSW params, distance metric) and restore behavior
> are still version-sensitive even for an export-format baseline. Don't float it to
> `:latest`.

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

Retrieval changes are gated on a frozen, cross-model-validated gold set
(`mcp/tests/gold/`). The battery (`mcp/tests/battery_sweep.py`) records latency
(median/IQR over repeats), per-call counts, and machine-readable JSON results;
past sweeps are kept in `mcp/tests/battery_results/`.

---

## When the plan comes apart (troubleshooting)

> _"This is a very complicated case."_

**The run log is `~/.local/share/embeddington/run.log`** (or `$EMBEDDINGTON_HOME/run.log`).
Every command the wizard runs, the error from any that fails, and the nightly update job all
land there — including `install.sh`'s Python bootstrap, so a failed install and a failed run
are in one file. It lives outside the clone deliberately, because re-cloning is the first
thing people try. Nothing secret goes in it. **If you're reporting a problem, this is the
file to send.**

**`embeddington-setup --check`** is the doctor: it reports health, changes nothing, and
exits 0 or 1.

Every installer failure prints an `[EMB-nn]` code with a fix line attached.
**[docs/troubleshooting.md](docs/troubleshooting.md)** has the full entry for each one,
plus the two failures that have no code: the schema-version gate, and embeddington not
showing up in Claude.

## Who's got the papers (license & data provenance)

> _"Is this your homework, Larry?"_

The **code** in this repository — the consumer CLI and the bundled MCP server — is licensed
under the **Apache License 2.0**. See [`LICENSE`](LICENSE).

The **data** is derived, not original. Both the vectors and the graph are extracted from
**[ServiceNow/ServiceNowDocs](https://github.com/ServiceNow/ServiceNowDocs)** — ServiceNow's
own platform documentation, © ServiceNow, published under the Apache License 2.0. The
derived artifacts shipped here (Qdrant chunk embeddings, `entities_v3`, `relationships_v3`)
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
embeddington distributes is **data** — a Qdrant export bundle (vectors + payloads + the
collection config to rebuild it), an ArangoDB dump, and daily diffs. No engine source, no
binaries, no images.

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
