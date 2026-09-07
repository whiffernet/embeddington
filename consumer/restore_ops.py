"""Real restore adapters for the consumer baseline import (the heavy IO half of Plan 3b).

These turn a downloaded baseline into a live local stack:
  * ``decompress`` — ``.tar.zst`` -> extracted dir, plain ``.zst`` -> decompressed file
    (uses the ``zstandard`` lib, so no ``zstd`` CLI is required).
  * ``restore_qdrant_snapshot`` — recovers a Qdrant snapshot via the upload API.
  * ``restore_arango_dump`` — ``arangorestore`` into the local Arango (creates the db).
    The dump directory is restored as-is: arangorestore names collections however the
    dump's SOURCE database named them, so a v3 baseline's dump already contains
    ``entities_v3``/``relationships_v3`` -- this function needs no schema parameter.
  * ``ensure_named_graph`` — creates the baseline's named graph over its resolved
    entities/relationships collections (arangodump can't carry named-graph
    definitions, but embeddington's traversal tools require one).
  * ``drop_old_schema_collections`` — after a successful restore onto a NEW schema
    generation, removes the previous generation's now-orphaned collections/graph so
    an install never carries two full copies of the KG side by side.
  * ``make_baseline_importer`` — composes the above (plus ``lexical_index``'s warm-up)
    into the ``baseline_importer`` callable that ``consumer.updater.update`` invokes
    on a fresh install, resolving every name from the baseline entry's ``kg_schema``
    field via ``embeddington.apply.schema_names``.

System dependencies (a consumer already has these to run the stack): ``docker`` for
``arangorestore`` and ``curl`` for the streamed (large) Qdrant snapshot upload.
"""

import subprocess
import tarfile
from pathlib import Path

import zstandard

from consumer import lexical_index, writers
from consumer.baseline_import import GRAPH_NAME, import_baseline
from embeddington.apply import schema_names
from embeddington.format import bundle

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


def restore_qdrant_bundle(qdrant_url, collection, bundle_path, coll_cfg):
    """Restore a full-export baseline: DROP and recreate the collection from the
    manifest's config, then stream-upsert every point record. Never buffers the
    bundle; skips the header and non-point records defensively.

    The drop is what makes this a baseline rather than a bulk upsert. Without
    it a baseline applied to an existing install adds its points beside the
    ones already stored, leaving two generations of every document live.

    Args:
        qdrant_url: Base URL of the local Qdrant (e.g. http://localhost:6333).
        collection: Target collection name.
        bundle_path: Path to the (still-compressed) ``.jsonl.zst`` export bundle.
        coll_cfg: The manifest's ``qdrant_collection`` dict (size/distance/hnsw_*).
    """
    writer = writers.QdrantConsumerWriter.connect(qdrant_url, collection)
    # REPLACE, not merge. A baseline is a complete statement of the corpus, so
    # applying one into a populated install must not leave the previous
    # generation beside it -- create_collection is a no-op when the collection
    # exists, which would do exactly that.
    writer.recreate_collection(
        size=coll_cfg["size"],
        distance=coll_cfg["distance"],
        hnsw_m=coll_cfg["hnsw_m"],
        hnsw_ef_construct=coll_cfg["hnsw_ef_construct"],
    )
    writer.upsert_points(
        (r["id"], r["vector"], r["payload"])
        for r in bundle.read_bundle(bundle_path)
        if r.get("kind") == "point" and r.get("op") == "upsert"
    )


REDACTED = "***"


def redact_argv(cmd, secret_flags):
    """A copy of `cmd` with the value after each secret-bearing flag replaced.

    Args:
        cmd: the argv list.
        secret_flags: flags whose FOLLOWING element is a secret.

    Returns:
        A new list, safe to put in an error message or a log.
    """
    safe, redact_next = [], False
    for part in cmd:
        safe.append(REDACTED if redact_next else part)
        redact_next = part in secret_flags
    return safe


def _run_without_leaking_secrets(cmd, *, secret_flags):
    """Run `cmd`, raising an error whose text carries no credential.

    `subprocess.run(check=True)` raises CalledProcessError, and that exception's string
    contains the ENTIRE argv — including any password in it. The installer turns an
    unexpected exception into EMB-45 by interpolating the exception, and the nightly job
    redirects that into ~/embeddington-update.log. So one ordinary restore failure wrote
    the ArangoDB root password, in plaintext, into a file that then sits there.

    Raising our own error with a redacted command keeps everything that made the original
    message useful — the failing command and the tool's own stderr — and drops only the
    part nobody needed to see.

    Args:
        cmd: argv list to execute.
        secret_flags: flags whose following argument must never appear in an error.

    Raises:
        RuntimeError: on a non-zero exit, with the command redacted.
    """
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode == 0:
        return result

    safe_cmd = redact_argv(cmd, secret_flags)
    stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
    # The tool can echo its own arguments back at us, so scrub the secrets out of its
    # output too rather than trusting it to be quiet about them.
    for flag in secret_flags:
        if flag in cmd:
            value = cmd[cmd.index(flag) + 1]
            if value:
                stderr = stderr.replace(value, REDACTED)
    detail = f": {stderr}" if stderr else ""
    raise RuntimeError(
        f"{safe_cmd[0]} exited {result.returncode} running {' '.join(safe_cmd)}{detail}"
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
    _run_without_leaking_secrets(
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
        secret_flags=("--server.password",),
    )


def ensure_named_graph(
    arango_url,
    db,
    username,
    password,
    *,
    graph_name=GRAPH_NAME,
    entities="entities_v2",
    relationships="relationships_v2",
):
    """Create the named graph over the resolved collections, if absent (idempotent).

    arangodump does not carry named-graph definitions, but embeddington's traversal
    tools (kg_neighbors / kg_path) require one over the entities/relationships pair.

    Args:
        arango_url: Base URL of the local Arango.
        db: Database holding the entities/relationships collections.
        username/password: Arango credentials.
        graph_name: Name of the named graph to create (default: the pre-cutover v2
            graph name, byte-identical to today's behavior).
        entities: Name of the vertex collection (default: the pre-cutover v2 name).
        relationships: Name of the edge collection (default: the pre-cutover v2 name).
    """
    from arango import ArangoClient

    database = ArangoClient(hosts=arango_url).db(db, username=username, password=password)
    if database.has_graph(graph_name):
        return
    graph = database.create_graph(graph_name)
    graph.create_edge_definition(
        edge_collection=relationships,
        from_vertex_collections=[entities],
        to_vertex_collections=[entities],
    )


def drop_old_schema_collections(arango_url, db, username, password, names):
    """Remove a previous KG schema generation's graph + collections (idempotent).

    Called once a restore has landed a NEW schema generation's collections and named
    graph successfully, so an install never carries two full copies of the KG side by
    side. Dropping in place -- rather than restoring into a fresh database and
    swapping -- is the simpler of the two options the existing restore code supports:
    ``restore_arango_dump`` always targets the SAME database by name, and every other
    ArangoDB-touching piece of the consumer/installer (writers, uninstall, doctor
    checks) already assumes one database, so a rename-and-swap would mean re-pointing
    all of them instead of one drop step here.

    Every deletion uses ``ignore_missing=True``: a second v3 restore in a row (a
    re-baseline after compaction, or ``--force-baseline``) finds nothing left of the
    old generation to drop, and that must be a silent no-op, not a failure.

    Args:
        arango_url: Base URL of the local Arango.
        db: Database holding the KG collections.
        username/password: Arango credentials.
        names: A resolved names dict (``schema_names.resolve_schema_names``'s shape)
            for the generation to REMOVE -- not the one just restored.
    """
    from arango import ArangoClient

    database = ArangoClient(hosts=arango_url).db(db, username=username, password=password)
    # drop_collections=False: the collections are dropped explicitly below, by name
    # from `names`, rather than by whatever the graph definition happens to reference.
    database.delete_graph(names["graph"], ignore_missing=True, drop_collections=False)
    database.delete_collection(names["entities"], ignore_missing=True)
    database.delete_collection(names["relationships"], ignore_missing=True)


def make_baseline_importer(
    release_client,
    work_dir,
    qdrant_url,
    collection,
    arango_url,
    db,
    username,
    password,
    *,
    arango_writer=None,
    persist_kg_schema=None,
):
    """Build the ``baseline_importer`` callable that ``updater.update`` calls on first run.

    Composes download (checksum-verified by the release client) + decompress + restore +
    named-graph creation + the lexical-index warm-up into a single
    ``callable(baseline_entry) -> {"head_sha", "chunk_text_status"}`` via
    ``consumer.baseline_import.import_baseline``.

    ``updater.update`` calls this importer, then -- in the SAME call, without
    returning in between -- applies any diffs published after the restored baseline
    using the writers it was given up front (spec: ``consumer/updater.py``'s
    baseline-then-diffs fallthrough; this project does not modify that file). When a
    restore lands a NEW kg_schema generation, those trailing diffs must land in the
    NEW collections, not the ones the writer was originally constructed against --
    hence ``arango_writer``: if given, it is retargeted (``ArangoConsumerWriter.retarget``)
    to the resolved names the moment the restore succeeds, before anything else
    (including the old-schema drop) runs. ``persist_kg_schema``, if given, is called
    with the resolved kg_schema string (``"v2"``/``"v3"``) at that same point, so a
    later failure in the diff-apply loop can never leave the on-disk record behind
    what the stores actually hold.

    Args:
        release_client: See the other positional args below (unchanged).
        work_dir: Scratch directory for downloads.
        qdrant_url: Base URL of the local Qdrant.
        collection: Qdrant collection name.
        arango_url: Base URL of the local Arango.
        db: Arango database name.
        username: Arango username.
        password: Arango password.
        arango_writer: Optional ``ArangoConsumerWriter`` (or anything exposing
            ``.retarget(entities=, relationships=)``) to re-point at the resolved
            collections immediately after a successful restore -- the SAME instance
            ``updater.update``'s subsequent diff-apply loop uses. ``None`` (e.g. in a
            test that doesn't thread one through) skips this step.
        persist_kg_schema: Optional ``callable(kg_schema: str)`` invoked with the
            resolved kg_schema right after retargeting, before the old-schema drop.
            ``None`` skips this step.

    Returns:
        A callable taking one manifest baseline entry, restoring it locally, and
        returning the import result dict (see ``import_baseline``).
    """

    def _import(baseline_entry):
        # Per-format Qdrant restore (Task 5's manifest `format` field): a snapshot-format
        # entry decompresses then uploads via the snapshot API (transition-window default,
        # collection created implicitly); a bundle-format entry streams the compressed
        # export directly, creating the collection explicitly from its manifest config.
        if baseline_entry.get("format") == "bundle":
            restore_q = lambda p: restore_qdrant_bundle(  # noqa: E731
                qdrant_url, collection, p, baseline_entry["qdrant_collection"]
            )
        else:
            restore_q = lambda p: restore_qdrant_snapshot(  # noqa: E731
                qdrant_url, collection, decompress(p)
            )

        # kg_schema (Task 5's manifest field): absent -> v2, byte-identical to today.
        # `restore_arango_dump` needs no name of its own (the dump's own collection
        # names win, unconditionally); only the NAMED GRAPH must be told which
        # collections to define itself over, since arangodump can't carry it.
        names = schema_names.resolve_schema_names(baseline_entry.get("kg_schema"))

        result = import_baseline(
            baseline_entry,
            work_dir,
            download_asset=lambda tag, asset, dest, sha: release_client.download_asset(
                tag, asset, dest, sha
            ),
            decompress=decompress,
            restore_qdrant=restore_q,
            restore_arango=lambda dump: restore_arango_dump(
                arango_url, db, username, password, dump
            ),
            ensure_graph=lambda: ensure_named_graph(
                arango_url,
                db,
                username,
                password,
                graph_name=names["graph"],
                entities=names["entities"],
                relationships=names["relationships"],
            ),
            ensure_lexical_index=lambda: lexical_index.ensure_chunk_text_index(
                qdrant_url, collection
            ),
        )

        # Retarget the writer `updater.update`'s own diff-apply loop is about to use
        # -- in this SAME call, right after this function returns -- BEFORE anything
        # else, including the old-schema drop below. Without this, a v3 baseline
        # followed by trailing diffs in one update() applies them through the
        # writer's ORIGINAL (pre-restore) collections, which the drop is about to
        # remove out from under it.
        if arango_writer is not None:
            arango_writer.retarget(entities=names["entities"], relationships=names["relationships"])
        # Persisted HERE, not after updater.update() returns: a failure anywhere in
        # the diff-apply loop that follows this function (in the same update() call)
        # would otherwise leave the on-disk record behind what the stores actually
        # hold, or -- because the diff loop is what would fail -- never record it at
        # all.
        if persist_kg_schema is not None:
            persist_kg_schema(baseline_entry.get("kg_schema") or "v2")

        # A successful restore onto anything other than v2 orphans the v2 collections
        # (the dump this run just restored never touches them) -- drop them so the
        # install doesn't carry two full copies of the KG. A same-generation restore
        # (kg_schema absent/"v2", the overwhelming common case pre-cutover) resolves
        # identically to `old_names` and this is skipped entirely.
        old_names = schema_names.resolve_schema_names("v2")
        if names != old_names:
            drop_old_schema_collections(arango_url, db, username, password, old_names)

        # import_baseline() stays a pure orchestrator (its own docstring's promise);
        # this is the IO layer, so the one visible trace an ordinary `update` run
        # leaves for the warm-up lives here, not threaded into updater.update's
        # structured receipt -- deliberately deferred (team-lead-confirmed):
        # updater.py is ~20 tightly-scenario-tested behaviors, not worth the
        # blast radius for a status ordinary callers can already see via this print.
        print(f"chunk_text index: {result['chunk_text_status']}")
        return result

    return _import
