"""Run the knowledge-graph import by calling the consumer's own machinery.

This module is a CALLER of consumer.updater.update — all cursor, adoption, and guard
semantics are the consumer's, untouched. Its jobs are wiring (same objects
consumer/cli.py builds), a status spinner, and translating exceptions to EMB codes.

The spec asks for "Rich progress" here; per the non-goal of never modifying the
updater, that is realized as an indeterminate status spinner with elapsed time, not
per-byte progress bars (which would require callbacks the updater doesn't have).
"""

import os
import urllib.error
from pathlib import Path

from consumer import release_client, restore_ops, state_paths, updater, writers
from consumer.fetcher import HttpFetcher
from embeddington.errors import ChecksumError, EmbeddingtonError
from installer.errors import SetupError

QDRANT_URL = "http://localhost:6333"
ARANGO_URL = "http://localhost:8529"
COLLECTION = "technology"
ARANGO_DB = "technology_kg"


def _production_wiring(repo_root, password, repo):
    """Build the exact objects consumer/cli.py's _cmd_update builds."""
    rc = release_client.ReleaseClient(HttpFetcher(), repo=repo)
    qdrant = writers.QdrantConsumerWriter.connect(QDRANT_URL, COLLECTION)
    arango = writers.ArangoConsumerWriter.connect(ARANGO_URL, ARANGO_DB, "root", password)
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
        )
    else:
        rc, qdrant, arango, importer = wiring_fn(repo_root, password, repo)

    try:
        with console.status(
            "[cyan]Rolling the graph forward... first run pulls ~828 MB — the Dude abides.[/cyan]"
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
