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
    instead of silently re-downloading ~828 MB over data that is already present. A restore
    we started ourselves and never finished is exempted via a ``<cursor>.restoring`` sentinel,
    so an interrupted baseline stays re-runnable.

``force_baseline`` deliberately bypasses both.
"""

import os
import sys
import time
from pathlib import Path

from consumer.cursor_store import read_cursor, write_cursor
from embeddington import apply_diff, plan_update
from embeddington.errors import EmbeddingtonError
from embeddington.format import bundle as bundle_mod


class BaselineRequired(EmbeddingtonError):
    """A baseline restore is needed but no baseline_importer was provided."""


class BaselineRefused(EmbeddingtonError):
    """A baseline restore was needed, but both stores already hold data.

    Almost always means the cursor file went missing (or lives somewhere this run did not
    look) while the data is still there. Restoring would re-download the whole baseline over
    data that is already present.
    """


def restore_sentinel_path(cursor_path):
    """Return the marker file that says "a baseline restore is in progress".

    A baseline restore takes minutes (Qdrant snapshot, then arangorestore) and writes the
    cursor only at the very end. Killed mid-arangorestore, it leaves BOTH stores looking
    populated with no cursor -- indistinguishable, from counts alone, from a healthy install
    whose cursor was lost. The sentinel removes the ambiguity: if it is there, WE started a
    restore that never finished, so the retry must be allowed through the guard.

    Because its presence SKIPS the guard, the sentinel must never outlive the restore it
    describes -- an orphan is a permanent guard-off switch. ``_clear_restore_sentinel`` is
    therefore called on every path where a cursor is known-good, not only after an import.

    Args:
        cursor_path: Path to the cursor file.

    Returns:
        The sentinel path (``<cursor_path>.restoring``).
    """
    p = Path(cursor_path)
    return p.with_name(p.name + ".restoring")


def _write_restore_sentinel(cursor_path):
    """Mark a baseline restore as in-flight, self-describing enough to be recognised later.

    The content (pid + start time) is not read by anything here -- the file's EXISTENCE is the
    signal. It is written so that a human (or a future expiry policy) staring at an orphan can
    tell which process claimed it and when, instead of an anonymous empty file.

    Args:
        cursor_path: Path to the cursor file the restore will write.

    Returns:
        The sentinel path that was written.
    """
    sentinel = restore_sentinel_path(cursor_path)
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(
        f"pid={os.getpid()} started={time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n",
        encoding="utf-8",
    )
    return sentinel


def _clear_restore_sentinel(cursor_path):
    """Remove the in-flight marker. Safe to call when there is none.

    Args:
        cursor_path: Path to the cursor file.
    """
    restore_sentinel_path(cursor_path).unlink(missing_ok=True)


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
        (sha, source_path, read_paths): the adopted sha and where it came from (both None when
        no candidate holds a usable sha), plus EVERY candidate that held a sha at all. The
        losers matter as much as the winner -- see ``_retire_legacy_cursors``.
    """
    best = None  # (remaining, sha, path) -- on-chain, nearest HEAD
    fallback = None  # (sha, path) -- exists but off-chain
    read_paths = []  # every candidate that held a sha, winner or not
    for path in legacy_cursors:
        sha = read_cursor(path)
        if not sha:
            continue
        read_paths.append(path)
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
        return best[1], best[2], read_paths
    if fallback is not None:
        return fallback[0], fallback[1], read_paths
    return None, None, read_paths


def _retire_legacy_cursors(paths):
    """Rename EVERY pre-v0.2 cursor we read to ``<name>.migrated`` so none is re-adopted.

    Retiring only the winner leaves the losers on disk as fully-eligible future adoption
    candidates -- including the stale ``$HOME/data/.cursor`` the old cron line created. Once
    the chain compacts past such a loser and the state cursor goes missing (a $HOME restore
    that skipped ~/.local/share; EMBEDDINGTON_HOME exported in .bashrc but not inherited by
    cron), adoption resurrects that dead sha as its off-chain fallback, the cursor is truthy,
    THE GUARD IS SKIPPED, and the baseline is re-downloaded over a healthy store. Retiring all
    of them means a missing state cursor always lands on the guard instead.

    The rename preserves the user's file (nothing is deleted). Failure to rename is not fatal
    -- the migration itself already succeeded, and aborting an otherwise-good update over a
    read-only clone would be worse -- but it IS reported, because a silent failure here leaves
    exactly the stale candidate this function exists to remove.

    Args:
        paths: The legacy cursor file Paths that were read this run.
    """
    for path in paths:
        try:
            path.rename(path.with_name(path.name + ".migrated"))
        except OSError as exc:
            print(
                f"warning: could not retire the legacy cursor {path} ({exc}). "
                "Rename or delete it yourself, or it may be adopted again later.",
                file=sys.stderr,
            )


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
            cursor, adopted_from, read_paths = _adopt_legacy_cursor(
                legacy_cursors, manifest, supported_major
            )
            if cursor:
                write_cursor(cursor_path, cursor)  # persist it: an at-HEAD sha writes nothing else
                _retire_legacy_cursors(read_paths)
        if cursor:
            # A cursor is known-good, so no restore is in flight: any sentinel is an ORPHAN
            # left by an earlier crash that the user then recovered from some other way. Left
            # alone it would sit there forever silently disabling the guard, and the next
            # missing cursor (cron / $XDG_DATA_HOME) would re-download 828 MB over live data.
            _clear_restore_sentinel(cursor_path)

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
        # stay re-runnable -- hence BOTH stores must look populated to refuse. The sentinel
        # covers the other half of that window: a restore killed DURING arangorestore leaves
        # both stores populated, and refusing THAT would strand the user on --force-baseline.
        if not cursor and not force_baseline and not restore_sentinel_path(cursor_path).exists():
            points = qdrant.point_count()
            if points > 0 and arango.entity_count() > 0:
                cursor_path = Path(cursor_path)
                raise BaselineRefused(
                    f"Qdrant collection '{qdrant.collection}' already has {points:,} points "
                    f"and ArangoDB already holds entities, but no cursor was found at "
                    f"{cursor_path}.\n\n"
                    "  Your data is there; only the file recording its version is missing.\n"
                    "  Restoring now would re-download the full baseline (~828 MB) for nothing.\n\n"
                    "  - cursor elsewhere?  If an older install left a cursor in some other\n"
                    "                       directory, COPY it into place and re-run:\n"
                    f"                         mkdir -p {cursor_path.parent}\n"
                    f"                         cp /path/to/data/.cursor {cursor_path}\n"
                    "  - really want it?    Re-run with --force-baseline to discard the local\n"
                    "                       version and re-restore the full baseline (~828 MB)."
                )
        if baseline_importer is None:
            raise BaselineRequired(f"baseline {plan.baseline['tag']} required; run import-baseline")
        # Mark the restore as in-flight BEFORE it starts, so a kill anywhere inside it (the
        # multi-minute arangorestore especially) is recognisable as our own unfinished work.
        _write_restore_sentinel(cursor_path)
        baseline_importer(plan.baseline)
        write_cursor(cursor_path, plan.baseline["head_sha"])
        _clear_restore_sentinel(cursor_path)  # cursor is durable now: the restore is complete

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
