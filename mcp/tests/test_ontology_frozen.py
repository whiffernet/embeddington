"""Pin the frozen ontology-measurement constants (spec §4/M1).

Frozen BEFORE any metric is computed so no metric can be improved by editing
the threshold it is measured against. A failure here means a constant changed —
which invalidates the committed baseline snapshot and requires cutting a new
one, not updating this test.
"""

import ontology_frozen as F


def test_hub_threshold_is_frozen():
    assert F.HUB_DEGREE_THRESHOLD == 1000


def test_pair_set_is_frozen_and_type_stratified():
    assert F.PAIR_SET_SIZE == 500
    assert F.PAIR_TYPES == ("Product", "Module", "Feature")


def test_name_length_thresholds_are_frozen():
    assert F.MIN_NAME_CHARS == 4
    assert F.MAX_GENERIC_WORD_CHARS == 12


def test_known_releases_is_frozen():
    assert len(F.KNOWN_RELEASES) == 28
    assert "zurich" in F.KNOWN_RELEASES


def test_release_normalization_strips_punctuation_and_space():
    # "WashingtonDC Patch 9a" must resolve against the spaced "washington dc".
    assert F.normalize_release("Washington DC") == "washingtondc"
    assert F.normalize_release("WashingtonDC Patch 9a") == "washingtondcpatch9a"
    assert F.normalize_release("  Zurich  ") == "zurich"


def test_binding_matches_the_frozen_gold_baseline():
    import gold_pools

    assert F.BASELINE_TAG == gold_pools.EXPECTED_BINDING["baseline"]
    assert F.EXPECTED_ENTITIES == gold_pools.EXPECTED_BINDING["entities"]
    assert F.EXPECTED_EDGES == gold_pools.EXPECTED_BINDING["edges"]
