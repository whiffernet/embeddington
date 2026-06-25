"""Turn (local cursor, manifest) into an ordered, gap-checked, version-gated update plan.

Walks the diff chain link by link (each diff's prev_sha must equal the running
cursor), never skipping. If the cursor is not reachable in the retained chain, falls
back to the latest baseline + the diffs after it (spec §5.3–5.4).
"""

from dataclasses import dataclass
from typing import Literal

from embeddington.errors import ChainGapError, SchemaVersionError
from embeddington.format.manifest import validate_manifest

SUPPORTED_SCHEMA_MAJOR = 1


@dataclass
class UpdatePlan:
    """The computed route to current.

    mode: "up_to_date" | "diffs" | "baseline".
    baseline: the baseline entry to restore first (only when mode == "baseline").
    diffs: ordered diff entries to apply after any baseline.
    """

    mode: Literal["up_to_date", "diffs", "baseline"]
    baseline: dict | None
    diffs: list


def _chain_from(start_sha, diffs):
    """Return the contiguous ordered diffs whose chain begins at start_sha.

    Returns None if start_sha is not the prev_sha of any retained diff (i.e. the
    cursor is unreachable). Raises ChainGapError if a started chain is broken.

    Args:
        start_sha: The SHA to begin the chain walk from.
        diffs: The full ordered list of diff entries from the manifest.

    Returns:
        A list of diff entries forming the chain, or None if start_sha is not found.

    Raises:
        ChainGapError: If the chain starts but then has a broken prev_sha link.
    """
    start_idx = next((i for i, d in enumerate(diffs) if d["prev_sha"] == start_sha), None)
    if start_idx is None:
        return None
    chain = []
    running = start_sha
    for diff in diffs[start_idx:]:
        if diff["prev_sha"] != running:
            raise ChainGapError(f"diff chain gap: expected prev {running}, got {diff['prev_sha']}")
        chain.append(diff)
        running = diff["head_sha"]
    return chain


def plan_update(cursor, manifest, supported_major=SUPPORTED_SCHEMA_MAJOR):
    """Compute the update plan for a client at ``cursor`` against ``manifest``.

    Args:
        cursor: The client's current head_sha, or None for a fresh install.
        manifest: A validated-or-validatable manifest dict.
        supported_major: The schema major version this client understands.

    Returns:
        UpdatePlan.

    Raises:
        SchemaVersionError: If the manifest's schema major exceeds supported_major.
        ChainGapError: If the diff chain reachable from the cursor is broken.
        ManifestError: If the manifest is malformed (re-raised from validate_manifest).
    """
    validate_manifest(manifest)
    major = int(str(manifest["schema_version"]).split(".")[0])
    if major > supported_major:
        raise SchemaVersionError(
            f"manifest schema major {major} exceeds supported {supported_major}; re-baseline"
        )

    diffs = manifest["diffs"]
    latest_baseline = manifest["baselines"][-1]
    head = diffs[-1]["head_sha"] if diffs else latest_baseline["head_sha"]

    # A None cursor never equals a valid head SHA, so this is safe before the None guard.
    if cursor == head:
        return UpdatePlan("up_to_date", None, [])

    if cursor is not None:
        chain = _chain_from(cursor, diffs)
        if chain is not None:
            return UpdatePlan("diffs", None, chain)
        # cursor unreachable in the retained chain -> fall through to baseline

    after_baseline = _chain_from(latest_baseline["head_sha"], diffs) or []
    return UpdatePlan("baseline", latest_baseline, after_baseline)
