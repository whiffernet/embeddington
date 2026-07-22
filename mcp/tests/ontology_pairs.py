"""Frozen entity-pair set for the path metrics (spec §4/M2).

Selection is deterministic — AQL sorts by ``_key``, never RAND() — so the same
restored stack always yields the same pairs. Selection is also STRATIFIED BY
TYPE: an earlier draft took the first N of a globally ``_key``-sorted list,
which yielded 100% Feature endpoints because "feature__" sorts before
"module__"/"product__" and Feature alone exceeded the limit.

Pairing crosses strata (stratum i paired against stratum i+1, cyclically) so
the set exercises Product-Module, Module-Feature and Feature-Product paths
rather than only within-type ones.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from ontology_frozen import PAIR_SET_SIZE, PAIR_TYPES

PAIRS_PATH = Path(__file__).resolve().parent / "ontology" / "pairs.json"


def fingerprint_pairs(pairs: list[dict]) -> str:
    """Return a stable sha256 over pair identities only.

    Order-independent and blind to display-only fields, so re-exporting with
    different names does not invalidate the frozen set.

    Args:
        pairs: Pair dicts carrying at least ``from_id`` and ``to_id``.

    Returns:
        A string of the form ``"sha256:<64-hex-digits>"``.
    """
    identities = sorted((p["from_id"], p["to_id"]) for p in pairs)
    return "sha256:" + hashlib.sha256(json.dumps(identities, sort_keys=True).encode()).hexdigest()


def select_pairs(db: Any, size: int = PAIR_SET_SIZE) -> list[dict]:
    """Deterministically select type-stratified entity pairs.

    Endpoints are drawn from each type in PAIR_TYPES with degree in [5, 200] —
    connected enough that a path plausibly exists, not so connected that the
    endpoint is itself a hub. Each type contributes an equal quota.

    Args:
        db: python-arango database handle.
        size: Number of pairs to produce.

    Returns:
        Pair dicts with ``from_id``/``to_id``, their types, and display names.

    Raises:
        ValueError: If any stratum cannot supply its quota — a silent shortfall
            would produce an undersized frozen artifact that later runs would
            compare against without noticing.
    """
    per_type = -(-size // len(PAIR_TYPES))  # ceiling division
    strata: list[list[dict]] = []
    for entity_type in PAIR_TYPES:
        rows = list(
            db.aql.execute(
                """
                FOR e IN entities_v2
                  FILTER e.type == @type
                  LET d = LENGTH(FOR x IN 1..1 ANY e relationships_v2 RETURN 1)
                  FILTER d >= 5 AND d <= 200
                  SORT e._key
                  LIMIT @limit
                  RETURN {id: e._id, name: e.name, type: e.type}
                """,
                bind_vars={"type": entity_type, "limit": per_type},
            )
        )
        if len(rows) < per_type:
            raise ValueError(
                f"stratum {entity_type!r} supplied {len(rows)} of {per_type} "
                "required candidates — the pair set would be undersized"
            )
        strata.append(rows)

    pairs: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for index in range(per_type):
        for stratum_index in range(len(strata)):
            left = strata[stratum_index][index]
            right = strata[(stratum_index + 1) % len(strata)][index]
            if left["id"] == right["id"]:
                continue
            key = (left["id"], right["id"])
            if key in seen:
                continue
            seen.add(key)
            pairs.append(
                {
                    "from_id": left["id"],
                    "to_id": right["id"],
                    "from_type": left["type"],
                    "to_type": right["type"],
                    "from_name": left["name"],
                    "to_name": right["name"],
                }
            )
            if len(pairs) == size:
                return pairs
    return pairs


def load_pairs(path: Path = PAIRS_PATH) -> dict:
    """Load the committed frozen pair artifact.

    Args:
        path: Path to pairs.json.

    Returns:
        The parsed artifact: ``binding``, ``size``, ``fingerprint``, ``pairs``.
    """
    return json.loads(path.read_text())
