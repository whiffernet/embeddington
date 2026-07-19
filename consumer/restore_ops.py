"""Real restore adapters for the consumer baseline import (the heavy IO half of Plan 3b).

These turn a downloaded baseline into a live local stack:
  * ``decompress`` — ``.tar.zst`` -> extracted dir, plain ``.zst`` -> decompressed file
    (uses the ``zstandard`` lib, so no ``zstd`` CLI is required).
  * ``restore_qdrant_snapshot`` — recovers a Qdrant snapshot via the upload API.
  * ``restore_arango_dump`` — ``arangorestore`` into the local Arango (creates the db).
  * ``ensure_named_graph`` — creates ``servicenow_graph_v2`` (arangodump can't carry it,
    but embeddington's traversal tools require it).
  * ``make_baseline_importer`` — composes the above (plus ``lexical_index``'s warm-up)
    into the ``baseline_importer`` callable that ``consumer.updater.update`` invokes
    on a fresh install.

System dependencies (a consumer already has these to run the stack): ``docker`` for
``arangorestore`` and ``curl`` for the streamed (large) Qdrant snapshot upload.
"""

import subprocess
import tarfile
from pathlib import Path

import zstandard

from consumer import lexical_index
from consumer.baseline_import import GRAPH_NAME, import_baseline

# Arango image used for the one-shot arangorestore — pin to the consumer stack's version.
ARANGO_IMAGE = "arangodb/arangodb:3.12.4"


def decompress(path):
    """Decompress a ``.zst`` file or extract a ``.tar.zst`` archive.

    Args:
        path: Path to a ``.zst`` (-> decompressed file) or ``.tar.zst`` (-> dir).

    Returns:
        The path (str) to the decompressed file, or the extracted directory.
    """
    path = Path(path)
    dctx = zstandard.ZstdDecompressor()
    if path.name.endswith(".tar.zst"):
        tar_path = path.with_suffix("")  # drop ".zst" -> ".tar"
        with open(path, "rb") as src, open(tar_path, "wb") as dst:
            dctx.copy_stream(src, dst)
        out_dir = path.parent / (path.name[: -len(".tar.zst")] + "-dump")
        if out_dir.exists():
            subprocess.run(["rm", "-rf", str(out_dir)], check=True)
        out_dir.mkdir(parents=True)
        with tarfile.open(tar_path) as tf:
            tf.extractall(out_dir, filter="data")  # reject unsafe members/paths
        # arangodump output may sit one level down; return the dir that holds the dump.
        inner = [p for p in out_dir.iterdir() if p.is_dir()]
        return str(inner[0] if len(inner) == 1 else out_dir)
    out = path.with_suffix("")  # drop ".zst"
    with open(path, "rb") as src, open(out, "wb") as dst:
        dctx.copy_stream(src, dst)
    return str(out)


def restore_qdrant_snapshot(qdrant_url, collection, snapshot_path):
    """Recover a Qdrant snapshot into ``collection`` via the upload API (creates it).

    Uses ``curl`` to stream the (large) snapshot file as multipart form data, so the
    whole snapshot is never buffered in memory.

    Args:
        qdrant_url: Base URL of the local Qdrant (e.g. http://localhost:6333).
        collection: Target collection name.
        snapshot_path: Path to the decompressed ``.snapshot`` file.
    """
    subprocess.run(
        [
            "curl",
            "-fsS",
            "-X",
            "POST",
            f"{qdrant_url}/collections/{collection}/snapshots/upload?priority=snapshot",
            "-H",
            "Content-Type:multipart/form-data",
            "-F",
            f"snapshot=@{snapshot_path}",
        ],
        check=True,
        capture_output=True,
    )


def restore_arango_dump(arango_url, db, username, password, dump_dir, image=ARANGO_IMAGE):
    """arangorestore a dump into the local Arango database (creating it if needed).

    Runs a one-shot ``arangorestore`` from the pinned Arango image on the host network,
    so it works against the published 8529 port without a persistent helper container.

    Args:
        arango_url: Base URL of the local Arango (e.g. http://localhost:8529).
        db: Target database name (created via ``--create-database``).
        username/password: Arango credentials (the stack root user).
        dump_dir: Host path to the arangodump output directory.
        image: Arango image providing ``arangorestore`` (pinned to the stack version).
    """
    host = arango_url.replace("http://", "").replace("https://", "")
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "host",
            "-v",
            f"{Path(dump_dir).resolve()}:/dump:ro",
            image,
            "arangorestore",
            "--server.endpoint",
            f"tcp://{host}",
            "--server.username",
            username,
            "--server.password",
            password,
            "--server.database",
            db,
            "--create-database",
            "true",
            "--input-directory",
            "/dump",
        ],
        check=True,
        capture_output=True,
    )


def ensure_named_graph(arango_url, db, username, password):
    """Create the ``servicenow_graph_v2`` named graph if absent (idempotent).

    arangodump does not carry named-graph definitions, but embeddington's traversal
    tools (kg_neighbors / kg_path) require this one over relationships_v2.

    Args:
        arango_url: Base URL of the local Arango.
        db: Database holding entities_v2 / relationships_v2.
        username/password: Arango credentials.
    """
    from arango import ArangoClient

    database = ArangoClient(hosts=arango_url).db(db, username=username, password=password)
    if database.has_graph(GRAPH_NAME):
        return
    graph = database.create_graph(GRAPH_NAME)
    graph.create_edge_definition(
        edge_collection="relationships_v2",
        from_vertex_collections=["entities_v2"],
        to_vertex_collections=["entities_v2"],
    )


def make_baseline_importer(
    release_client, work_dir, qdrant_url, collection, arango_url, db, username, password
):
    """Build the ``baseline_importer`` callable that ``updater.update`` calls on first run.

    Composes download (checksum-verified by the release client) + decompress + restore +
    named-graph creation + the lexical-index warm-up into a single
    ``callable(baseline_entry) -> {"head_sha", "chunk_text_status"}`` via
    ``consumer.baseline_import.import_baseline``.

    Returns:
        A callable taking one manifest baseline entry, restoring it locally, and
        returning the import result dict (see ``import_baseline``).
    """

    def _import(baseline_entry):
        result = import_baseline(
            baseline_entry,
            work_dir,
            download_asset=lambda tag, asset, dest, sha: release_client.download_asset(
                tag, asset, dest, sha
            ),
            decompress=decompress,
            restore_qdrant=lambda snap: restore_qdrant_snapshot(qdrant_url, collection, snap),
            restore_arango=lambda dump: restore_arango_dump(
                arango_url, db, username, password, dump
            ),
            ensure_graph=lambda: ensure_named_graph(arango_url, db, username, password),
            ensure_lexical_index=lambda: lexical_index.ensure_chunk_text_index(
                qdrant_url, collection
            ),
        )
        # import_baseline() stays a pure orchestrator (its own docstring's promise);
        # this is the IO layer, so the one visible trace an ordinary `update` run
        # leaves for the warm-up lives here, not threaded into updater.update's
        # structured receipt (deliberately deferred -- see task-4-report.md).
        print(f"chunk_text index: {result['chunk_text_status']}")
        return result

    return _import
