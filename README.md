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

A bundled **claudeGraph** MCP server lets Claude query the graph directly (vector search +
graph traversal). It uses your local stores and Claude for reasoning — there is no
dependency on any external model or API.

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

**1. Clone (with the claudeGraph submodule):**

```bash
git clone --recurse-submodules https://github.com/whiffernet/embeddington.git
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

## Query with Claude (claudeGraph MCP)

The `claudegraph/` submodule is a stdio MCP server exposing vector search and graph
traversal over your local stores. See **`claudegraph/README.md`** for setup; point its
environment at the stack you started above:

| Variable          | Value                       |
| ----------------- | --------------------------- |
| `QDRANT_URL`      | `http://localhost:6333`     |
| `ARANGO_URL`      | `http://localhost:8529`     |
| `ARANGO_DATABASE` | `technology_kg`             |
| `ARANGO_USER`     | `root`                      |
| `ARANGO_PASSWORD` | your `ARANGO_ROOT_PASSWORD` |

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
