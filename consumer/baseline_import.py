"""Consumer baseline import: download a baseline Release, restore it locally, build the graph.

The first-run path (Plan 3b): download the baseline assets (checksum-verified by the
release client), decompress the Arango dump, restore Qdrant + the Arango dump into the
user's local stores, create the named graph ``servicenow_graph_v2`` — which
``arangodump`` does NOT capture but embeddington's traversal tools require — and
finally warm the consumer-local ``chunk_text`` lexical index (a bare vector snapshot
restore always drops it; see ``consumer/lexical_index.py``). Returns the baseline's
head_sha and the lexical index's resulting status so the caller can seed the cursor
and report it.

The Qdrant leg is format-dependent (snapshot vs. export bundle, see Task 5's manifest
``format`` field), so ``restore_qdrant`` receives the DOWNLOADED (still-compressed)
asset path and owns its own interpretation: a snapshot-format callable decompresses
then uploads, a bundle-format callable streams the ``.zst`` directly. The Arango leg
has no such split -- it is always decompressed here, once, before ``restore_arango``.

All heavy operations are injected, so the orchestration is pure and unit-testable; the
real adapters (download/decompress/restore/graph/lexical-index) are wired by the CLI /
Plan-3b ops.
"""

from pathlib import Path

from consumer.fetcher import discard

GRAPH_NAME = "servicenow_graph_v2"


def import_baseline(
    baseline_entry,
    work_dir,
    download_asset,
    decompress,
    restore_qdrant,
    restore_arango,
    ensure_graph,
    ensure_lexical_index,
):
    """Download, restore, graph-init, and warm-index one baseline.

    Args:
        baseline_entry: a manifest baseline entry (tag, head_sha, assets, sha256, ...).
        work_dir: directory to download/decompress into.
        download_asset: callable(tag, asset_name, dest, sha256) -> downloaded path
            (verifies the checksum; e.g. release_client.download_asset).
        decompress: callable(path) -> decompressed path (".zst" -> file, ".tar.zst" -> dir).
            Applied here only to the Arango asset; the Qdrant asset is handed to
            ``restore_qdrant`` still compressed (see module docstring).
        restore_qdrant: callable(q_zst) -> restores the Qdrant collection from the
            downloaded (compressed) asset; owns its own decompress/stream interpretation.
        restore_arango: callable(dump_dir) -> arangorestore into the local db.
        ensure_graph: callable() -> create the ``servicenow_graph_v2`` named graph if absent.
        ensure_lexical_index: callable() -> str, warms the chunk_text field + full-text
            index and returns its resulting status ("ready"/"building"/"absent"/
            "unavailable"). Run LAST, after the graph, since it reads the collection
            ``restore_qdrant`` just populated.

    Returns:
        dict {"head_sha", "chunk_text_status"}: the baseline's head_sha (to seed the
        local cursor) and the lexical index's resulting status (to report to the user;
        never raised for a degraded status -- see ``ensure_lexical_index``'s contract).
    """
    work = Path(work_dir)
    tag = baseline_entry["tag"]

    # Everything this restore creates goes in one directory we own, so cleaning up is
    # "remove that directory" rather than "remember every intermediate". Enumerating them
    # does not hold: decompress writes an uncompressed .tar beside the archive, extracts
    # into a sibling -dump/ and hands back a directory ONE LEVEL DOWN inside it, and the
    # Qdrant path decompresses an 861 MB .zst into a larger .snapshot. Deleting the two
    # downloads would have left every one of those behind — the biggest ones.
    #
    # It also keeps cleanup off any path the user chose: --work-dir can point anywhere, so
    # "empty the work directory" is not ours to do. This directory is.
    scratch = work / f"baseline-{tag}"
    scratch.mkdir(parents=True, exist_ok=True)

    q_asset = baseline_entry["assets"]["qdrant"]
    a_asset = baseline_entry["assets"]["arango"]
    q_zst = download_asset(tag, q_asset, scratch / q_asset, baseline_entry["sha256"]["qdrant"])
    a_zst = download_asset(tag, a_asset, scratch / a_asset, baseline_entry["sha256"]["arango"])

    dump_dir = decompress(a_zst)

    restore_qdrant(q_zst)
    restore_arango(dump_dir)
    ensure_graph()  # the named graph arangodump can't carry — required for embeddington
    chunk_text_status = ensure_lexical_index()  # warm it now, not at the first MCP request

    # Everything above is now a duplicate of what is in the stores — comfortably over a
    # gigabyte of it, in a directory the user never looks at. Removed only after the
    # restore has actually succeeded: on a failure it stays put as evidence, and re-running
    # re-downloads regardless (the fetcher never reuses an existing file).
    discard(scratch)

    return {"head_sha": baseline_entry["head_sha"], "chunk_text_status": chunk_text_status}
