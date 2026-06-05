# `consumer/` — pull the shared graph down to your machine

> _"New information has come to light, man."_ — The Dude

This is the side of Embeddington that runs on _your_ machine. It stands up a small local
stack (Qdrant + ArangoDB + a `bge-m3` embedding server), then pulls the published
`servicenow` `technology` knowledge graph from GitHub Releases and applies it into those
local stores. Nothing here ever writes to a production endpoint — it abides by your stack
and your stack alone.

The whole thing runs from one command: **`embeddington-consume update`**. On a fresh
install it restores the latest _baseline_ (a full Qdrant snapshot + Arango dump + the
`servicenow_graph_v2` named graph), then applies any newer _diffs_. On later runs it
applies only the diffs since your local cursor. It's idempotent and resumable, so an
interrupted run picks right back up.

## What's in the folder

Brandt would want you oriented, so here's the tour — he's a good man, and thorough:

| File                 | Purpose                                                                                                                                                                  |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `docker-compose.yml` | Runs the local stack: Qdrant (`6333`), ArangoDB (`8529`), and the `embed` server (`8100`). Start here.                                                                   |
| `.env.example`       | Copy to `.env` and set `ARANGO_ROOT_PASSWORD` before bringing the stack up.                                                                                              |
| `cli.py`             | The entry point (`embeddington-consume update`). Parses args and wires the release client + writers + restore ops together.                                              |
| `release_client.py`  | Resolves GitHub Release asset URLs, fetches the `manifest.json`, and downloads + checksum-verifies each asset.                                                           |
| `fetcher.py`         | The transport the release client uses: `GhFetcher` (via the `gh` CLI, default, works on private repos) or `HttpFetcher` (urllib + optional `GITHUB_TOKEN`).              |
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
