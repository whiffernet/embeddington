"""Collect and persist the frozen ontology baseline snapshot (spec §4/M1).

This is the "before" measurement every later Round A PR is compared against.
Run against the RESTORED BATTERY STACK, never prod: prod has drifted ~9%/~11%
from the frozen binding and its numbers are not comparable.

Usage:
    python3 tests/ontology_snapshot.py            # print, do not write
    python3 tests/ontology_snapshot.py --write    # write the committed artifact
"""

import argparse
import json
import os
from pathlib import Path
from typing import Any

import ontology_frozen as F
import ontology_metrics as M
import ontology_pairs as P

SNAPSHOT_PATH = Path(__file__).resolve().parent / "ontology" / "baseline-snapshot.json"


def collect(db: Any) -> dict:
    """Run every intrinsic and path metric against a restored stack.

    Args:
        db: python-arango database handle pointed at the battery stack.

    Returns:
        The full snapshot dict, ready to serialise.
    """
    pairs = P.load_pairs()["pairs"]
    return {
        "frozen": {
            "frozen_on": F.FROZEN_ON,
            "baseline": F.BASELINE_TAG,
            "hub_degree_threshold": F.HUB_DEGREE_THRESHOLD,
            "pair_set_size": F.PAIR_SET_SIZE,
            "pair_types": list(F.PAIR_TYPES),
            "min_name_chars": F.MIN_NAME_CHARS,
        },
        "non_independent_metrics": [
            "noise.noise_rate",
            "fragmentation.fragmentation_rate",
            "fragmentation.cross_type_concept_count",
        ],
        "fragmentation": M.fragmentation(db),
        "noise": M.noise(db),
        "topology": M.topology(db),
        "release_purity": M.release_purity(db),
        "paths": M.path_metrics(db, pairs, M.hub_ids(db)),
    }


def _db() -> Any:
    from arango import ArangoClient

    return ArangoClient(hosts=os.environ.get("BATTERY_ARANGO_URL", "http://localhost:19412")).db(
        os.environ.get("BATTERY_ARANGO_DB", "technology_kg"),
        username=os.environ.get("BATTERY_ARANGO_USER", "root"),
        password=os.environ["BATTERY_ARANGO_PASSWORD"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="write the committed snapshot artifact"
    )
    args = parser.parse_args()

    payload = json.dumps(collect(_db()), indent=2, sort_keys=True) + "\n"
    if args.write:
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(payload)
        print(f"wrote {SNAPSHOT_PATH}")
    else:
        print(payload)


if __name__ == "__main__":
    main()
