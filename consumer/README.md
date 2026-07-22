# `consumer/` — pull the shared graph down to your machine

> _"New information has come to light, man."_

This is the side of embeddington that runs on _your_ machine. It stands up a small local
stack (Qdrant + ArangoDB + a `bge-m3` embedding server), then pulls the published
`servicenow` `technology` knowledge graph from GitHub Releases and applies it into those
local stores. Nothing here ever writes to a production endpoint — it abides by your stack
and your stack alone.

The whole thing runs from one command: **`embeddington-consume update`**. On a fresh
install it restores the latest _baseline_ (a full Qdrant snapshot + Arango dump + the
`servicenow_graph_v2` named graph), then applies any newer _diffs_. On later runs it
applies only the diffs since your local cursor. It's idempotent, and resumable at _diff_
granularity — an interrupted baseline download restarts that one asset from zero (it
streams to disk, so it won't eat your RAM doing it).

## What's in the folder

Brandt would want you oriented, so here's the tour — he's a good man, and thorough:

| File                 | Purpose                                                                                                                                                                  |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `docker-compose.yml` | Runs the local stack: Qdrant (`6333`), ArangoDB (`8529`), and the `embed` server (`8100`). Start here.                                                                   |
| `.env.example`       | Copy to `.env` and set `ARANGO_ROOT_PASSWORD` before bringing the stack up.                                                                                              |
| `cli.py`             | The entry point (`embeddington-consume update`). Parses args and wires the release client + writers + restore ops together.                                              |
| `release_client.py`  | Resolves GitHub Release asset URLs, fetches the `manifest.json`, and downloads + checksum-verifies each asset.                                                           |
| `fetcher.py`         | The transport the release client uses: `HttpFetcher` (anonymous HTTPS; streams large assets to disk).                                                                    |
| `updater.py`         | The orchestrator. Plans baseline-vs-diffs from the manifest, applies each bundle, and advances the cursor only after a full apply.                                       |
| `writers.py`         | The local write adapters — `QdrantConsumerWriter` and `ArangoConsumerWriter` — that upsert/delete points, entities, and edges in your stores.                            |
| `cursor_store.py`    | Reads/writes the local `.cursor` file that tracks your current `head_sha` (your position in the diff chain).                                                             |
| `baseline_import.py` | Pure orchestration of a first-run baseline restore (download → decompress → restore Qdrant/Arango → create the named graph). All heavy ops are injected.                 |
| `restore_ops.py`     | The heavy IO half: real decompress, Qdrant snapshot upload, `arangorestore`, and `servicenow_graph_v2` creation; `make_baseline_importer` composes them for the updater. |
| `__init__.py`        | Package marker / docstring. This package ships to users; `publisher/` does not.                                                                                          |
| `embed/`             | A subfolder, not a file — the local `bge-m3` embedding service that powers query embedding for `vector_search`. It has its own README; read that for details.            |

## How it flows

1. **`docker-compose.yml`** brings up Qdrant, ArangoDB, and `embed` on your machine.
2. **`cli.py`** is the entry point. It builds a `fetcher`, a `ReleaseClient`, the two
   `writers`, and (via `restore_ops.make_baseline_importer`) a baseline importer.
3. **`updater.update()`** reads your **`cursor_store`**, fetches the manifest through the
   **`release_client`**, and plans the route: up-to-date, baseline, or diffs.
4. If a baseline is needed, **`baseline_import.import_baseline`** orchestrates it and the
   heavy lifting in **`restore_ops.py`** does the actual snapshot/dump restore plus the
   `servicenow_graph_v2` named graph (which `arangodump` can't carry).
5. Each diff bundle is downloaded, checksum-verified, applied through **`writers.py`**, and
   the cursor advances. Interrupted? It resumes from the last fully-applied diff.

When the run finishes, the cursor file ties the whole room together — that's your position
in the chain, ready for the next `update`.

## Targeting: what `update` writes to, and how to run more than one stack

`update` is destructive by design — it restores a baseline and applies diffs into whatever
Qdrant and ArangoDB it's pointed at. Where it's pointed is controlled entirely by flags:

| Flag           | Default                 | What it targets                           |
| -------------- | ----------------------- | ----------------------------------------- |
| `--qdrant-url` | `http://localhost:6333` | the Qdrant instance written to            |
| `--arango-url` | `http://localhost:8529` | the ArangoDB instance written to          |
| `--cursor`     | your per-user state dir | which position-in-the-chain gets advanced |

The environment supplies exactly one thing: the Arango password, via `ARANGO_ROOT_PASSWORD`
(or `ARANGO_PASSWORD`) — the same var `docker-compose.yml` reads, so one `.env` covers both.
Nothing else is read from the environment. That matters because it's the opposite of the
`mcp/` server in this repo, which _does_ read `QDRANT_URL` and `ARANGO_URL` straight from
the environment (see its README). If you're used to exporting those two vars to point the
MCP server somewhere, carrying that habit here does nothing useful — `update` will still
write to `localhost:6333`/`localhost:8529` (or wherever your last `--qdrant-url`/
`--arango-url` pointed) regardless of what's exported.

`update` also prints the targets it resolved — Qdrant, Arango, and the cursor path, each
marked `(default)` or `(explicit)` — before it checks reachability and before anything is
downloaded. Read that line before letting a run proceed. `ensure-index` prints the same for
the one thing it writes to: it materializes and indexes `chunk_text` on whatever
`--qdrant-url` resolves to (same default as `update`).

To run a genuinely isolated restore — a second collection, a scratch stack, whatever you
don't want touching your main one — pass all three targeting flags together. Omitting any
one of them falls back to the default, which is what you're trying to avoid:

```sh
embeddington-consume update \
  --qdrant-url http://localhost:7333 \
  --arango-url http://localhost:9529 \
  --cursor ./scratch/.cursor
```

Afterwards, confirm the stack you _didn't_ mean to touch is still what you expect —
checking your default collection's point count is the fastest sanity check:

```sh
curl -s http://localhost:6333/collections/technology | python3 -c \
  "import sys, json; print(json.load(sys.stdin)['result']['points_count'])"
```
