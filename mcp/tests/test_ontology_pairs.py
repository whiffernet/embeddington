"""Tests for the frozen entity-pair set used by the path metrics (spec §4/M2).

The set must be deterministic, fingerprinted, and type-stratified. Before/after
comparison is meaningless if the pair population drifts, and the metric is
unrepresentative if one entity type monopolises the sample.
"""

import ontology_pairs as P


def test_fingerprint_is_stable_for_same_pairs():
    pairs = [
        {"from_id": "entities_v2/a", "to_id": "entities_v2/b"},
        {"from_id": "entities_v2/c", "to_id": "entities_v2/d"},
    ]
    assert P.fingerprint_pairs(pairs) == P.fingerprint_pairs(list(pairs))


def test_fingerprint_is_order_independent():
    a = [
        {"from_id": "entities_v2/a", "to_id": "entities_v2/b"},
        {"from_id": "entities_v2/c", "to_id": "entities_v2/d"},
    ]
    assert P.fingerprint_pairs(a) == P.fingerprint_pairs(list(reversed(a)))


def test_fingerprint_changes_when_a_pair_changes():
    a = [{"from_id": "entities_v2/a", "to_id": "entities_v2/b"}]
    b = [{"from_id": "entities_v2/a", "to_id": "entities_v2/z"}]
    assert P.fingerprint_pairs(a) != P.fingerprint_pairs(b)


def test_fingerprint_ignores_display_only_fields():
    a = [{"from_id": "entities_v2/a", "to_id": "entities_v2/b", "from_name": "A"}]
    b = [{"from_id": "entities_v2/a", "to_id": "entities_v2/b", "from_name": "AAA"}]
    assert P.fingerprint_pairs(a) == P.fingerprint_pairs(b)


def test_committed_pairs_file_matches_its_own_fingerprint():
    data = P.load_pairs(P.PAIRS_PATH)
    assert data["fingerprint"] == P.fingerprint_pairs(data["pairs"])
    assert len(data["pairs"]) == data["size"]


def test_committed_pairs_are_bound_to_the_frozen_baseline():
    import ontology_frozen as F

    data = P.load_pairs(P.PAIRS_PATH)
    assert data["binding"]["baseline"] == F.BASELINE_TAG
    assert data["binding"]["entities"] == F.EXPECTED_ENTITIES
    assert data["binding"]["edges"] == F.EXPECTED_EDGES


def test_no_self_pairs_and_no_duplicates():
    data = P.load_pairs(P.PAIRS_PATH)
    seen = set()
    for pair in data["pairs"]:
        assert pair["from_id"] != pair["to_id"]
        key = (pair["from_id"], pair["to_id"])
        assert key not in seen
        seen.add(key)


def test_committed_pairs_are_type_stratified():
    """Every configured type must appear as an endpoint.

    An earlier draft sorted candidates by _key and took the first N, which
    produced 1000/1000 Feature candidates — "feature__" sorts before
    "module__"/"product__" and Feature alone exceeded the limit. Every pair was
    Feature-to-Feature from the alphabetically-earliest slice.
    """
    import ontology_frozen as F

    data = P.load_pairs(P.PAIRS_PATH)
    endpoint_types = {p["from_type"] for p in data["pairs"]}
    endpoint_types |= {p["to_type"] for p in data["pairs"]}
    assert endpoint_types == set(F.PAIR_TYPES)
    for expected in F.PAIR_TYPES:
        share = sum(1 for p in data["pairs"] if expected in (p["from_type"], p["to_type"])) / len(
            data["pairs"]
        )
        assert share > 0.10, f"{expected} under-represented at {share:.1%}"
