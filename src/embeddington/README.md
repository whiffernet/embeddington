# `embeddington` — the format + apply library

> _"This is not 'Nam. This is bowling. There are rules."_ — Walter, on the wire format

This is the small, dependency-light core that everyone agrees on. It defines **the
on-the-wire format** for shipping a ServiceNow `technology` knowledge graph (a
[bge-m3] Qdrant vector collection + Arango entities/relationships) and **an idempotent,
resumable apply path** for ingesting it.

Two parties build on this library:

- the **publisher** (an abstract role, _not_ in this repo) produces baselines and
  SHA-chained diffs, and
- the **consumer** plans an update from its local cursor and applies the records into
  its own Qdrant + Arango.

Both sides speak the exact same record and manifest shapes, so nobody has to guess.
Walter would approve.

## What's here

| Path                  | Purpose                                                                                                                                             |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `__init__.py`         | The public surface: `apply_diff`, `plan_update`, `UpdatePlan`, and the error classes.                                                               |
| `errors.py`           | The typed exception hierarchy (`EmbeddingtonError` and friends).                                                                                    |
| `format/records.py`   | Record builders/encoder/decoder. Kinds: `header`, plus `point` / `entity` / `edge` in both `upsert` and `delete` flavors. One JSON object per line. |
| `format/bundle.py`    | Read/write a bundle as newline-delimited JSON, transparently zstd-compressed when the path ends in `.zst`.                                          |
| `format/manifest.py`  | The manifest: a list of `baselines` + SHA-chained `diffs`, plus `validate_manifest`, IO helpers, and sha256 asset-integrity (`verify_asset`).       |
| `apply/cursor.py`     | `plan_update` — turns a local cursor + manifest into an ordered, gap-checked `UpdatePlan` (baseline vs. diffs).                                     |
| `apply/apply_diff.py` | Applies a bundle's records idempotently to injected Qdrant/Arango writers.                                                                          |
| `apply/protocols.py`  | The narrow `QdrantWriter` / `ArangoWriter` interfaces apply depends on.                                                                             |

## `format/` — the contract

`records.py` is the vocabulary: a header carries `schema_version`, the `prev_sha` it
applies on top of, the `head_sha` it advances to, and counts. Upserts carry their keys
(`id` for a Qdrant point, `_key` for an Arango entity/edge); deletes are tombstones.
`bundle.py` just lines those records up as newline-delimited JSON and (optionally)
zstd-compresses the result.

`manifest.py` is the table of contents. It lists one or more **baselines** and a chain
of **diffs** where each diff's `prev_sha` links to the previous one's `head_sha`. Assets
carry a `sha256`, and `verify_asset` refuses anything that doesn't match — _mark it
zero_ if the checksum is wrong.

## `apply/` — idempotent and resumable

`plan_update(cursor, manifest)` walks the diff chain link by link from your cursor,
never skipping a link (a broken `prev_sha` raises `ChainGapError`). New information has
come to light? You get a `"diffs"` plan. Cursor unreachable in the retained chain (or a
fresh install)? You fall back to the latest `"baseline"` plus the diffs after it. Already
current? `"up_to_date"`. A manifest from a newer major schema gets a `SchemaVersionError`
rather than a confident wrong guess.

`apply_diff(records, qdrant, arango)` replays a bundle into the writers you inject (see
`protocols.py` — the real adapters live elsewhere). **Every write is keyed**, so an
upsert overwrites cleanly and a delete never errors on an already-absent target. That's
the whole design virtue:

> _The Dude abides_ — re-running a bundle is always safe, man. Crash mid-apply, rerun,
> no harm done.

Because of that, the consumer only advances its cursor **after** a diff has fully
applied. Half-applied bundles cost you nothing but a replay.

[bge-m3]: https://huggingface.co/BAAI/bge-m3
