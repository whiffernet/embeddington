"""Live battery gate for the extrinsic-floor pipeline (spec §4/M2, revised).

Skips unless EMBEDDINGTON_BATTERY=1. Mirrors test_ontology_live.py's pattern:
the committed extrinsic-pairs.json must be reproducible from the same
restored stack that produced ontology_pairs.PAIRS_PATH, since it is derived
from that file, not from a fresh query.
"""

import json
import os

import ontology_extrinsic_pairs as E
import ontology_frozen as F
import ontology_pair_labels as O
import ontology_pairs as P
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("EMBEDDINGTON_BATTERY") != "1",
    reason="live battery: set EMBEDDINGTON_BATTERY=1 with the stack restored",
)


def test_committed_extrinsic_pairs_matches_a_fresh_selection():
    committed = json.loads((O.ONTOLOGY_DIR / "extrinsic-pairs.json").read_text())
    pool = P.load_pairs()["pairs"]
    fresh = E.select_extrinsic_subset(
        pool, F.EXTRINSIC_SET_SIZE - F.EXTRINSIC_DUPLICATE_COUNT, F.EXTRINSIC_DUPLICATE_COUNT
    )
    assert E.fingerprint_extrinsic_set(fresh["pairs"]) == committed["fingerprint"]


def test_committed_extrinsic_pairs_is_bound_to_the_frozen_pool():
    committed = json.loads((O.ONTOLOGY_DIR / "extrinsic-pairs.json").read_text())
    pool_data = P.load_pairs()
    # Every from_id/to_id in the committed subset must exist in the current pool --
    # catches a drift where pairs.json was regenerated but extrinsic-pairs.json was not.
    pool_ids = {p["from_id"] for p in pool_data["pairs"]} | {p["to_id"] for p in pool_data["pairs"]}
    for pair in committed["pairs"]:
        assert pair["from_id"] in pool_ids
        assert pair["to_id"] in pool_ids
