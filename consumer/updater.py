"""The consumer update orchestrator: route -> download -> apply -> advance cursor.

Uses Plan-1's ``plan_update`` to decide the route and ``apply_diff`` to apply each bundle.
The cursor advances only AFTER a diff fully applies, and applies are idempotent, so an
interrupted run resumes safely (spec §5.3). Downloads are checksum-verified by the release
client. Baseline restore is delegated to an injected ``baseline_importer``.

The stores are machine-global while the cursor used to be per-directory, so a cursor could
go "missing" while the data was very much still there. Two behaviors protect the expensive
baseline route:

  * ADOPTION -- when the cursor file is absent, a pre-v0.2 ``data/.cursor`` is adopted
    rather than treating the install as fresh. Existing users migrate without re-downloading.
  * THE GUARD -- when no cursor is found anywhere AND both stores already hold data, refuse
    instead of silently re-downloading ~828 MB over data that is already present.

``force_baseline`` deliberately bypasses both.
"""

from pathlib import Path

from consumer.cursor_store import read_cursor, write_cursor
from embeddington import apply_diff, plan_update
from embeddington.errors import EmbeddingtonError
from embeddington.format import bundle as bundle_mod


class BaselineRequired(EmbeddingtonError):
    """A baseline restore is needed but no baseline_importer was provided."""


class BaselineRefused(EmbeddingtonError):
    """A baseline restore was needed, but both stores already hold data.

    Almost always means the command ran somewhere other than the user's install, so the
    cursor was not found. Restoring would re-download the whole baseline over data that is
    already present.
    """


def _adopt_legacy_cursor(legacy_cursors, manifest, supported_major):
    """Pick the best pre-v0.2 cursor to migrate forward.

    Preference is the candidate furthest along the diff chain -- i.e. with the fewest diffs
    left to apply. Asking ``plan_update`` for each candidate's plan answers that directly and
    keeps all chain-walking in the (pure) planner.

    A candidate whose plan comes back ``"baseline"`` is not reachable on the retained chain
    (the post-compaction case). It is still adopted, as a fallback, when nothing better
    exists: the user demonstrably HAS an install, so ``plan_update`` should be allowed to
    route them to a normal re-baseline instead of the guard refusing them outright.

    Args:
        legacy_cursors: Iterable of candidate cursor file Paths.
        manifest: The fetched manifest dict.
        supported_major: Schema major this client supports.

    Returns:
        (sha, source_path), or (None, None) when no candidate holds a usable sha.
    """
    best = None  # (remaining, sha, path) -- on-chain, nearest HEAD
    fallback = None  # (sha, path) -- exists but off-chain
    for path in legacy_cursors:
        sha = read_cursor(path)
        if not sha:
            continue
        try:
            plan = plan_update(sha, manifest, supported_major)
        except EmbeddingtonError:
            continue  # broken chain / unsupported schema -> not a usable cursor
        if plan.mode == "baseline":
            if fallback is None:
                fallback = (sha, path)  # off-chain: keep it only as the fallback
            continue
        remaining = len(plan.diffs)
        if best is None or remaining < best[0]:
            best = (remaining, sha, path)
    if best is not None:
        return best[1], best[2]
    if fallback is not None:
        return fallback
    return None, None


def update(
    release_client,
    qdrant,
    arango,
    cursor_path,
    work_dir,
    baseline_importer=None,
    supported_major=1,
    diffs_tag="diffs",
    *,
    legacy_cursors=(),
    force_baseline=False,
):
    """Bring the local stores current with the published manifest.

    Args:
        release_client: A consumer.release_client.ReleaseClient.
        qdrant: A QdrantConsumerWriter.
        arango: An ArangoConsumerWriter.
        cursor_path: Path to the local .cursor file.
        work_dir: Directory to download diff bundles into.
        baseline_importer: Optional callable(baseline_entry) that restores a baseline and
            leaves the stores at baseline.head_sha. Required when the plan needs one.
        supported_major: Schema major this client supports.
        diffs_tag: The release tag holding diff assets + manifest.
        legacy_cursors: Candidate pre-v0.2 cursor paths to adopt when cursor_path is absent
            (see consumer.state_paths.legacy_cursor_candidates).
        force_baseline: Ignore the local cursor entirely and re-restore the baseline. This
            is the ONLY way to recover a corrupted store whose cursor is still intact, so it
            must short-circuit before the cursor is even read.

    Returns:
        dict {"mode", "applied", "cursor", "baseline", "adopted_from"}.

    Raises:
        BaselineRequired: A baseline is needed but no importer was given.
        BaselineRefused: A baseline is needed, no cursor was found anywhere, and both stores
            already hold data (and force_baseline is False).
        ChainGapError / SchemaVersionError: From plan_update (re-baseline signals).
    """
    manifest = release_client.fetch_manifest()

    adopted_from = None
    if force_baseline:
        cursor = None  # ignore any cursor: the point is to restore regardless
    else:
        cursor = read_cursor(cursor_path)
        if not cursor:
            cursor, adopted_from = _adopt_legacy_cursor(legacy_cursors, manifest, supported_major)
            if cursor:
                write_cursor(cursor_path, cursor)

    plan = plan_update(cursor, manifest, supported_major)

    if plan.mode == "up_to_date":
        return {
            "mode": "up_to_date",
            "applied": 0,
            "cursor": cursor,
            "baseline": None,
            "adopted_from": adopted_from,
        }

    if plan.mode == "baseline":
        # Guard ONLY the "no cursor found anywhere" case. A cursor that exists but has
        # fallen off the retained chain is a legitimate post-compaction re-baseline, and a
        # half-restored store (Qdrant written, Arango not, cursor not yet advanced) must
        # stay re-runnable -- hence BOTH stores must look populated to refuse.
        if not cursor and not force_baseline:
            points = qdrant.point_count()
            if points > 0 and arango.entity_count() > 0:
                raise BaselineRefused(
                    f"Qdrant collection '{qdrant.collection}' already has {points:,} points, "
                    f"but no cursor was found at {cursor_path}.\n\n"
                    "  You are probably running from a directory that isn't your install, "
                    "or the cursor was deleted.\n\n"
                    "  - upgrading?   cd to your clone and re-run (it will adopt the old\n"
                    "                 data/.cursor automatically)\n"
                    "  - deliberate?  re-run with --force-baseline to re-restore the full\n"
                    "                 baseline (~828 MB)"
                )
        if baseline_importer is None:
            raise BaselineRequired(f"baseline {plan.baseline['tag']} required; run import-baseline")
        baseline_importer(plan.baseline)
        write_cursor(cursor_path, plan.baseline["head_sha"])

    applied = 0
    for diff in plan.diffs:
        dest = Path(work_dir) / diff["asset"]
        release_client.download_asset(diffs_tag, diff["asset"], dest, diff["sha256"])
        apply_diff(bundle_mod.read_bundle(dest), qdrant, arango)
        write_cursor(cursor_path, diff["head_sha"])  # advance only after full apply
        applied += 1

    return {
        "mode": plan.mode,
        "applied": applied,
        "cursor": read_cursor(cursor_path),
        "baseline": plan.baseline,
        "adopted_from": adopted_from,
    }
