"""Frozen measurement constants for the ontology round (spec §4/M1).

Every value here is pinned BEFORE any metric is computed, so a metric can never
be improved by editing the threshold it is measured against.

Changing ANY value invalidates tests/ontology/baseline-snapshot.json and
requires cutting a new snapshot. It is not a routine edit.
"""

import re

FROZEN_ON = "2026-07-21"

# --- Stack binding -------------------------------------------------------
# Must match gold_pools.EXPECTED_BINDING: the snapshot and the gold set must
# describe the same restored stack or their numbers cannot be compared.
BASELINE_TAG = "baseline-2026-07b"
EXPECTED_ENTITIES = 310364
EXPECTED_EDGES = 683651

# --- Metric thresholds ---------------------------------------------------
# Degree at which an entity is a hub. hub_concentration derives its node set
# from THIS value rather than a separate top-K, so there is no second knob.
HUB_DEGREE_THRESHOLD = 1000

# Path-metric pair set. Large because these metrics need no labeling, so N is
# free. Stratified across PAIR_TYPES — an earlier draft sorted candidates by
# _key and took the first N, which yielded 100% Feature endpoints because
# "feature__" sorts before "module__"/"product__" and Feature alone exceeded
# the limit. Stratification is the fix; it is frozen here, not chosen later.
PAIR_SET_SIZE = 500
PAIR_TYPES = ("Product", "Module", "Feature")

MIN_NAME_CHARS = 4
MAX_GENERIC_WORD_CHARS = 12

# --- Known ServiceNow platform releases ----------------------------------
KNOWN_RELEASES = frozenset(
    {
        "aspen",
        "berlin",
        "calgary",
        "dublin",
        "eureka",
        "fuji",
        "geneva",
        "helsinki",
        "istanbul",
        "jakarta",
        "kingston",
        "london",
        "madrid",
        "new york",
        "orlando",
        "paris",
        "quebec",
        "rome",
        "san diego",
        "tokyo",
        "utah",
        "vancouver",
        "washington",
        "washington dc",
        "xanadu",
        "yokohama",
        "zurich",
        "australia",
    }
)

_NON_ALNUM_RELEASE = re.compile(r"[^a-z0-9]")


def normalize_release(name: str) -> str:
    """Casefold and strip every non-alphanumeric character.

    Applied to BOTH sides of the release comparison. Exact matching alone
    recognises only 27 of 1,315 Release entities (2.05%) because the corpus is
    dominated by decorated forms ("Zurich Patch 2", "Vancouver release"), and
    it misses "WashingtonDC Patch 9a" outright — an unambiguous platform
    release — because the frozen list spells it "washington dc". Normalising
    both sides then matching exact-or-prefix is the frozen rule.

    Args:
        name: A raw release name.

    Returns:
        The normalized comparison key.
    """
    return _NON_ALNUM_RELEASE.sub("", name.casefold())


# --- Extrinsic floor (spec §4/M2, revised 2026-07-22) ---------------------
# Size of the frozen extrinsic pair set, INCLUDING the blind noise-floor
# duplicates below. Drawn from the already-frozen, fingerprinted PAIR_SET_SIZE
# pool (pairs.json) rather than a fresh query — no new sampling decision, no
# new fingerprint-provenance question.
EXTRINSIC_SET_SIZE = 200

# How many of the EXTRINSIC_SET_SIZE entries are blind duplicates of another
# entry in the same set, used to measure the judge's own labeling noise floor
# (spec §4/M2, "Noise floor"). The judge is never told which entries repeat.
EXTRINSIC_DUPLICATE_COUNT = 10

# /embed?index= routing — must match the encoder that built the `technology`
# Qdrant collection (config.py:ALLOWED_QDRANT_COLLECTIONS), or similarity is
# garbage.
EMBED_INDEX = "technology"

# Embedding-signal confidence bands, validated against Erik's 30-pair v2
# labels (spec §2.7): consensus-zone auto-labeling at these bands matched his
# verdict 10/11 = 91%, clearing CONCORDANCE_BAR below. Not tuned on this run —
# they are the bands the validation was measured at.
EMBED_CONFIDENT_GOOD_PCT = 0.90  # percentile >= this -> "good" vote
EMBED_CONFIDENT_BAD_PCT = 0.60  # percentile < this -> "bad" vote (else "abstain")

# Minimum consensus-zone concordance against Erik's labels required before
# the pipeline is allowed to auto-label anything. Measured at 0.909 (10/11)
# on the seed set (mcp/tests/ontology/seed-validation.json) — see
# test_ontology_consensus.py's regression tripwire. If a future re-validation
# (prompt change, model swap) drops below this bar, auto-labeling must stop
# and every pair in the frozen set escalates — spec §4/M2 is explicit this is
# a real possible outcome, not a formality.
CONCORDANCE_BAR = 0.85
