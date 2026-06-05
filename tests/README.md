# Embeddington Tests

> _"This is not 'Nam. This is bowling. There are rules."_ — Walter

The Dude abides, but the test suite has rules. This folder is where every record,
bundle, manifest, and apply step gets graded before it touches a real graph. Mark it
zero if it doesn't pass.

## Running the suite

From the repo root:

```bash
pip install -e .[dev]
pytest
```

That's the whole ceremony. These are fast, dependency-light unit tests — every Qdrant
and Arango interaction runs against injected in-memory fakes (see `conftest.py`), so you
do **not** need a live `qdrant`, `arango`, or any network access to run them.

## What's covered

| Test group           | What it exercises                                                                                                                                                                                                                                                                                                                                                                                                   |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/format/`      | The on-disk wire format: `test_records.py` (header + upsert/delete record encode/decode roundtrips), `test_bundle.py` (zstd bundle read/write), `test_manifest.py` (baseline manifest + sha256 checksums).                                                                                                                                                                                                          |
| `tests/apply/`       | The apply engine: `test_apply_diff.py` and `test_idempotency.py` (applying a diff bundle, then re-applying it, yields identical state — no duplicates, no drift), `test_cursor.py` (cursor + `plan_update` selecting baselines/diffs from a manifest), `test_fakes.py` (the fakes themselves behave).                                                                                                               |
| `tests/consumer/`    | The consumer side end to end: `test_cli.py` (arg parsing + dispatch), `test_updater.py` (the update orchestration), `test_release_client.py` and `test_fetcher.py` (release lookup + asset fetch), `test_writers.py` (real-shape Qdrant/Arango writer adapters), `test_cursor_store.py` (persisted cursor state), `test_baseline_import.py` (snapshot/baseline import), `test_restore_ops.py` (restore operations). |
| `test_errors.py`     | The exception hierarchy — every error (`RecordError`, `ManifestError`, `ChecksumError`, `ChainGapError`, `SchemaVersionError`) subclasses `EmbeddingtonError` and carries its message.                                                                                                                                                                                                                              |
| `test_public_api.py` | The public import surface: `apply_diff`, `plan_update`, `UpdatePlan`, and `EmbeddingtonError` are importable straight off `embeddington`.                                                                                                                                                                                                                                                                           |
| `conftest.py`        | Shared fakes — in-memory stand-ins for the Qdrant `technology` collection and the Arango `entities_v2` / `relationships_v2` collections, injected as fixtures so tests never touch a live store. (`tests/consumer/conftest.py` adds fakes shaped like the real `qdrant_client` / Arango adapter call surface.)                                                                                                      |

_Am I wrong? No. Run `pytest` and find out for yourself._
