"""Frozen extrinsic pair subset for the M2 quality floor (spec §4/M2, revised 2026-07-22).

Drawn from the already-frozen, fingerprinted pool in ontology_pairs.PAIRS_PATH — no fresh Arango
query, no new sampling decision. Selection takes the pool's first `unique_size` pairs in file
order (itself deterministic — see ontology_pairs.py's own docstring) and appends
`duplicate_count` blind copies of the LAST `duplicate_count` unique pairs, each carrying a fresh
`n` and a `duplicate_of` pointer back to its original. The judge never sees `duplicate_of`; it
exists only so the finalize stage (ontology_pair_labels.py) can measure the judge's own blind
relabeling flip rate — the noise floor any real quality delta must clear.
"""

import hashlib
import json
from typing import Any


def select_extrinsic_subset(
    pool: list[dict], unique_size: int, duplicate_count: int
) -> dict[str, Any]:
    """Deterministically select a frozen subset with blind noise-floor duplicates.

    Args:
        pool: The full frozen pair pool (ontology_pairs.load_pairs()["pairs"]).
        unique_size: Number of distinct pairs to draw, taken in pool order.
        duplicate_count: How many of the last `unique_size` pairs to duplicate blind.

    Returns:
        Dict with ``size`` (unique_size + duplicate_count), ``unique_size``, and
        ``pairs`` — a list of dicts each carrying ``n`` (1-indexed, sequential),
        the original pair's from/to id/type/name fields, and ``duplicate_of``
        (the ``n`` of the original pair, or None for a unique entry).

    Raises:
        ValueError: If the pool has fewer than `unique_size` pairs.
    """
    if len(pool) < unique_size:
        raise ValueError(f"pool has {len(pool)} pairs, need {unique_size}")

    unique = []
    for i, pair in enumerate(pool[:unique_size]):
        unique.append(
            {
                "n": i + 1,
                "from_id": pair["from_id"],
                "to_id": pair["to_id"],
                "from_type": pair["from_type"],
                "to_type": pair["to_type"],
                "from_name": pair["from_name"],
                "to_name": pair["to_name"],
                "duplicate_of": None,
            }
        )

    duplicates = []
    source = unique[-duplicate_count:] if duplicate_count else []
    for i, original in enumerate(source):
        dupe = dict(original)
        dupe["n"] = unique_size + i + 1
        dupe["duplicate_of"] = original["n"]
        duplicates.append(dupe)

    all_pairs = unique + duplicates
    return {"size": len(all_pairs), "unique_size": unique_size, "pairs": all_pairs}


def fingerprint_extrinsic_set(pairs: list[dict]) -> str:
    """Return a stable sha256 over (n, from_id, to_id, duplicate_of) only.

    Args:
        pairs: Pair dicts as returned by select_extrinsic_subset.

    Returns:
        A string of the form ``"sha256:<64-hex-digits>"``.
    """
    identities = sorted((p["n"], p["from_id"], p["to_id"], p["duplicate_of"]) for p in pairs)
    return "sha256:" + hashlib.sha256(json.dumps(identities, sort_keys=True).encode()).hexdigest()
