"""Consumer baseline import: download a baseline Release, restore it locally, build the graph.

The first-run path (Plan 3b): download the baseline assets (checksum-verified by the
release client), decompress, restore the Qdrant snapshot + Arango dump into the user's
local stores, and create the named graph ``servicenow_graph_v2`` — which ``arangodump``
does NOT capture but claudeGraph's traversal tools require. Returns the baseline's
head_sha so the caller can seed the local cursor.

All heavy operations are injected, so the orchestration is pure and unit-testable; the
real adapters (download/decompress/restore/graph) are wired by the CLI / Plan-3b ops.
"""

from pathlib import Path

GRAPH_NAME = "servicenow_graph_v2"


def import_baseline(
    baseline_entry,
    work_dir,
    download_asset,
    decompress,
    restore_qdrant,
    restore_arango,
    ensure_graph,
):
    """Download, restore, and graph-init one baseline; return its head_sha.

    Args:
        baseline_entry: a manifest baseline entry (tag, head_sha, assets, sha256, ...).
        work_dir: directory to download/decompress into.
        download_asset: callable(tag, asset_name, dest, sha256) -> downloaded path
            (verifies the checksum; e.g. release_client.download_asset).
        decompress: callable(path) -> decompressed path (".zst" -> file, ".tar.zst" -> dir).
        restore_qdrant: callable(snapshot_path) -> restores the Qdrant collection.
        restore_arango: callable(dump_dir) -> arangorestore into the local db.
        ensure_graph: callable() -> create the ``servicenow_graph_v2`` named graph if absent.

    Returns:
        The baseline's head_sha (to seed the local cursor).
    """
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    tag = baseline_entry["tag"]

    q_asset = baseline_entry["assets"]["qdrant"]
    a_asset = baseline_entry["assets"]["arango"]
    q_zst = download_asset(
        tag, q_asset, work / q_asset, baseline_entry["sha256"]["qdrant"]
    )
    a_zst = download_asset(
        tag, a_asset, work / a_asset, baseline_entry["sha256"]["arango"]
    )

    snapshot_path = decompress(q_zst)
    dump_dir = decompress(a_zst)

    restore_qdrant(snapshot_path)
    restore_arango(dump_dir)
    ensure_graph()  # the named graph arangodump can't carry — required for claudeGraph

    return baseline_entry["head_sha"]
