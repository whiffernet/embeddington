"""Run the knowledge-graph import by calling the consumer's own machinery.

This module is a CALLER of consumer.updater.update — all cursor, adoption, and guard
semantics are the consumer's, untouched. Its jobs are wiring (same objects
consumer/cli.py builds), a status spinner, and translating exceptions to EMB codes.

The spec asks for "Rich progress" here; per the non-goal of never modifying the
updater, that is realized as an indeterminate status spinner with elapsed time, not
per-byte progress bars (which would require callbacks the updater doesn't have).

Schema-generation wiring (Task 5's ``kg_schema`` manifest field): the Arango writer
built here for DIFF application must target whichever collections this install's
stores are CURRENTLY named for -- which a baseline restore may have just changed. That
fact is recorded as ``EMBEDDINGTON_KG_SCHEMA``, read here before wiring the writer used
for THIS run, but recorded (both here and for the next run) from inside
``consumer.restore_ops.make_baseline_importer``'s importer, at the moment a restore
lands -- never after ``updater.update()`` returns, since a v3 baseline immediately
followed by trailing diffs (the common case: `cursor.plan_update` returns baseline +
every diff published after it in one shot) applies those diffs, in the SAME call,
before this function ever gets a result back. The importer both retargets the very
writer object that diff-apply loop uses (``ArangoConsumerWriter.retarget``) and
persists the schema, at that same point -- see ``restore_ops.make_baseline_importer``'s
docstring.

The value is written to two files: ``consumer/.env`` (which this module reads back on
the NEXT run) and ``mcp/.env`` (which the installed MCP server itself loads --
``mcp/server.py`` loads only ``mcp/.env`` plus a single hardcoded scan of
``consumer/.env`` for the Arango password specifically, so ``consumer/.env`` alone is
invisible to the server process; a schema-aware ``mcp/config.py`` has nowhere else to
read this from).
"""

import os
import sys
import urllib.error
from pathlib import Path

from consumer import (
    env_file,
    lexical_index,
    release_client,
    restore_ops,
    state_paths,
    updater,
    writers,
)
from consumer.fetcher import HttpFetcher
from embeddington.apply import schema_names
from embeddington.errors import ChecksumError, EmbeddingtonError, SchemaVersionError
from installer.errors import SetupError

QDRANT_URL = "http://localhost:6333"
ARANGO_URL = "http://localhost:8529"
COLLECTION = "technology"
ARANGO_DB = "technology_kg"

KG_SCHEMA_ENV_KEY = "EMBEDDINGTON_KG_SCHEMA"


def installed_kg_schema(consumer_dir):
    """The kg_schema this install's local Arango collections are currently named for.

    Args:
        consumer_dir: The clone's ``consumer/`` directory.

    Returns:
        The persisted value from ``consumer/.env``, or ``None`` when absent -- which
        ``schema_names.resolve_schema_names`` treats identically to ``"v2"``.
    """
    return env_file.read_key(Path(consumer_dir) / ".env", KG_SCHEMA_ENV_KEY)


def _persist_kg_schema(repo_root, kg_schema):
    """Best-effort: record the now-active schema where the NEXT run and the MCP see it.

    Writes ``EMBEDDINGTON_KG_SCHEMA`` to both ``consumer/.env`` (read back by
    ``installed_kg_schema`` before the next run's writer is built) and ``mcp/.env``
    (the installed MCP server's only general-purpose env source -- see the module
    docstring). Each file is attempted independently, so one failing does not stop
    the other. Never fatal -- a successful data update must not fail (or look like it
    failed) over a bookkeeping write.

    Called from inside ``restore_ops.make_baseline_importer``'s importer, at the
    moment a restore lands -- not after ``updater.update()`` returns (see the module
    docstring for why that was too late).

    Args:
        repo_root: The clone root (``consumer/`` and ``mcp/`` live beneath it).
        kg_schema: The value to persist (a baseline entry's ``kg_schema``, or "v2").
    """
    repo_root = Path(repo_root)
    for env_path in (repo_root / "consumer" / ".env", repo_root / "mcp" / ".env"):
        try:
            env_file.set_key(env_path, KG_SCHEMA_ENV_KEY, kg_schema)
        except OSError as exc:
            print(
                f"warning: could not record {KG_SCHEMA_ENV_KEY}={kg_schema} in "
                f"{env_path} ({exc}). The next update run re-derives it from the "
                "manifest, so this is not itself a failure.",
                file=sys.stderr,
            )


def _production_wiring(repo_root, password, repo):
    """Build the exact objects consumer/cli.py's _cmd_update builds.

    The Arango writer is schema-aware: its entities/relationships collections are
    resolved from this install's PERSISTED ``kg_schema`` (see ``installed_kg_schema``),
    not hardcoded to v2 -- a diff applied after a v3 re-baseline must land in
    ``entities_v3``/``relationships_v3``, not the (about to be dropped) v2 pair.
    """
    rc = release_client.ReleaseClient(HttpFetcher(), repo=repo)
    qdrant = writers.QdrantConsumerWriter.connect(QDRANT_URL, COLLECTION)
    names = schema_names.resolve_schema_names(installed_kg_schema(Path(repo_root) / "consumer"))
    arango = writers.ArangoConsumerWriter.connect(
        ARANGO_URL,
        ARANGO_DB,
        "root",
        password,
        entities=names["entities"],
        relationships=names["relationships"],
    )
    return rc, qdrant, arango


def run_import(
    console,
    repo_root,
    password,
    *,
    repo="whiffernet/embeddington",
    force_baseline=False,
    env=None,
    home=None,
    cwd=None,
    update_fn=None,
    wiring_fn=None,
):
    """Bring the stores current; return the updater's result dict.

    Args:
        console: rich Console (spinner).
        repo_root: the clone root (anchors legacy-cursor adoption).
        password: the local Arango root password (from stack.read_password).
        repo: GitHub owner/name for Releases.
        force_baseline: forwarded verbatim to updater.update.
        env: environment mapping (default: os.environ).
        home: home directory (default: Path.home()).
        cwd: current working directory (default: Path.cwd()).
        update_fn: test seam for updater.update (default: production updater).
        wiring_fn: test seam for building stores (default: production wiring).

    Returns:
        The updater.update result dict with keys: mode, applied, cursor, baseline,
        adopted_from.

    Raises:
        SetupError: EMB-41 network, EMB-42 checksum, EMB-43 guard refusal (the guard's
            own message is preserved), EMB-45 any other updater error.
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    cwd = Path.cwd() if cwd is None else Path(cwd)
    update_fn = updater.update if update_fn is None else update_fn

    cursor = state_paths.default_cursor_path(env, home)
    work_dir = state_paths.default_work_dir(env, home)
    legacy = state_paths.legacy_cursor_candidates(cwd, home)

    if wiring_fn is None:
        rc, qdrant, arango = _production_wiring(repo_root, password, repo)
        importer = restore_ops.make_baseline_importer(
            rc,
            work_dir,
            QDRANT_URL,
            COLLECTION,
            ARANGO_URL,
            ARANGO_DB,
            "root",
            password,
            arango_writer=arango,
            persist_kg_schema=lambda schema: _persist_kg_schema(repo_root, schema),
        )
    else:
        rc, qdrant, arango, importer = wiring_fn(repo_root, password, repo)

    try:
        with console.status(
            "[cyan]Rolling the graph forward... first run pulls ~1 GB — the Dude abides.[/cyan]"
        ):
            return update_fn(
                rc,
                qdrant,
                arango,
                cursor,
                work_dir,
                importer,
                legacy_cursors=legacy,
                force_baseline=force_baseline,
                ensure_index=lambda: lexical_index.incremental_chunk_text_index(
                    QDRANT_URL, COLLECTION
                ),
            )
    except updater.BaselineRefused as exc:
        raise SetupError(
            "EMB-43",
            str(exc),
            "If this store is healthy, copy your old cursor into the state dir; to "
            "deliberately re-restore everything, re-run with --force-baseline.",
        )
    except ChecksumError as exc:
        raise SetupError(
            "EMB-42",
            f"A downloaded asset failed checksum verification: {exc}",
            "Re-run — a corrupted download re-fetches cleanly. If it repeats, open an issue.",
        )
    except SchemaVersionError as exc:
        raise SetupError(
            "EMB-45",
            f"The published data format is newer than this install understands: {exc}",
            "Run Update from this wizard (or re-run the install one-liner) — it pulls "
            "new code first, then the data update resumes.",
        )
    except (updater.BaselineRequired, EmbeddingtonError) as exc:
        raise SetupError(
            "EMB-45",
            f"The updater could not complete: {exc}",
            "Re-run the installer; if it repeats, run `embeddington-consume update` "
            "directly for the full error and see the README's troubleshooting table.",
        )
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise SetupError(
            "EMB-41",
            f"A download failed: {exc}",
            "Check your connection and re-run — downloads resume/retry cleanly.",
        )
    except Exception as exc:
        raise SetupError(
            "EMB-45",
            f"The updater could not complete: {exc}",
            "Re-run the installer (imports are idempotent) — if the stores were mid-recovery "
            "after a restart this clears on a second try. If it repeats, run "
            "`embeddington-consume update` directly for the full error.",
        )


def proof_of_life(point_count, entity_count):
    """One real query against each store; EMB-44 if either is empty or unqueryable.

    Args:
        point_count: A callable that returns the number of vectors in Qdrant.
        entity_count: A callable that returns the number of entities in Arango.

    Returns:
        A tuple (point_count, entity_count) if both calls succeed and are nonzero.

    Raises:
        SetupError: EMB-44 if either counter is zero or if either raises an exception.

    Note:
        The counters are called wrapped: the real ``entity_count()`` raises on any
        non-absence Arango failure (401/500/503 — e.g. WAL recovery right after
        compose up), and an unwrapped call would crash the wizard with a traceback
        at its moment of triumph.
    """
    try:
        points, entities = point_count(), entity_count()
    except Exception as exc:
        raise SetupError(
            "EMB-44",
            f"Post-import verification could not query the stores: {exc}",
            "Give the containers a few seconds to settle and re-run "
            "`embeddington-setup --check`; if it persists, check "
            "`docker compose logs` in consumer/.",
        )
    if points <= 0 or entities <= 0:
        raise SetupError(
            "EMB-44",
            f"Post-import verification found {points} vectors and {entities} entities — "
            "at least one store looks empty.",
            "Re-run the installer (imports are idempotent). If it repeats, run "
            "`embeddington-consume update --force-baseline` for a clean restore.",
        )
    return points, entities
