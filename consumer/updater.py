"""The consumer update orchestrator: route -> download -> apply -> advance cursor.

Uses Plan-1's ``plan_update`` to decide the route and ``apply_diff`` to apply each
bundle. The cursor advances only AFTER a diff fully applies, and applies are idempotent,
so an interrupted run resumes safely (spec §5.3). Downloads are checksum-verified by the
release client. Baseline restore is delegated to an injected ``baseline_importer`` (the
heavy snapshot/dump restore is a separate ops adapter, Plan 3b).
"""

from pathlib import Path

from embeddington import apply_diff, plan_update
from embeddington.errors import EmbeddingtonError
from embeddington.format import bundle as bundle_mod
from consumer.cursor_store import read_cursor, write_cursor


class BaselineRequired(EmbeddingtonError):
    """A baseline restore is needed but no baseline_importer was provided."""


def update(
    release_client,
    qdrant,
    arango,
    cursor_path,
    work_dir,
    baseline_importer=None,
    supported_major=1,
    diffs_tag="diffs",
):
    """Bring the local stores current with the published manifest.

    Args:
        release_client: A consumer.release_client.ReleaseClient.
        qdrant: A QdrantWriter (consumer.writers.QdrantConsumerWriter).
        arango: An ArangoWriter (consumer.writers.ArangoConsumerWriter).
        cursor_path: Path to the local .cursor file.
        work_dir: Directory to download diff bundles into.
        baseline_importer: Optional callable(baseline_entry) that restores a baseline
            and leaves the stores at baseline.head_sha. Required when the plan needs one.
        supported_major: Schema major this client supports.
        diffs_tag: The release tag holding diff assets + manifest.

    Returns:
        dict {"mode": ..., "applied": <int>, "cursor": <head_sha or None>}.

    Raises:
        BaselineRequired: If a baseline is needed but no importer was given.
        ChainGapError / SchemaVersionError: From plan_update (re-baseline signals).
    """
    cursor = read_cursor(cursor_path)
    manifest = release_client.fetch_manifest()
    plan = plan_update(cursor, manifest, supported_major)

    if plan.mode == "up_to_date":
        return {"mode": "up_to_date", "applied": 0, "cursor": cursor}

    if plan.mode == "baseline":
        if baseline_importer is None:
            raise BaselineRequired(
                f"baseline {plan.baseline['tag']} required; run import-baseline"
            )
        baseline_importer(plan.baseline)
        write_cursor(cursor_path, plan.baseline["head_sha"])

    applied = 0
    for diff in plan.diffs:
        dest = Path(work_dir) / diff["asset"]
        release_client.download_asset(diffs_tag, diff["asset"], dest, diff["sha256"])
        apply_diff(bundle_mod.read_bundle(dest), qdrant, arango)
        write_cursor(cursor_path, diff["head_sha"])  # advance only after full apply
        applied += 1

    return {"mode": plan.mode, "applied": applied, "cursor": read_cursor(cursor_path)}
