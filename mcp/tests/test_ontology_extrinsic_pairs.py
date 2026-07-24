"""Tests for the frozen extrinsic pair subset (spec §4/M2, revised).

Selection must be deterministic (same pool -> same subset), must not draw
more unique pairs than the pool has, and the blind duplicates must be
genuinely indistinguishable from an original entry except for `n` and
`duplicate_of`.
"""

import ontology_extrinsic_pairs as E
import pytest


def _pool(n):
    return [
        {
            "from_id": f"entities_v2/f{i}",
            "to_id": f"entities_v2/t{i}",
            "from_type": "Feature",
            "to_type": "Product",
            "from_name": f"From {i}",
            "to_name": f"To {i}",
        }
        for i in range(n)
    ]


def test_selects_requested_unique_size_in_pool_order():
    result = E.select_extrinsic_subset(_pool(50), unique_size=20, duplicate_count=5)
    unique = [p for p in result["pairs"] if p["duplicate_of"] is None]
    assert len(unique) == 20
    assert [p["from_id"] for p in unique] == [f"entities_v2/f{i}" for i in range(20)]


def test_total_size_is_unique_plus_duplicates():
    result = E.select_extrinsic_subset(_pool(50), unique_size=20, duplicate_count=5)
    assert result["size"] == 25
    assert result["unique_size"] == 20
    assert len(result["pairs"]) == 25


def test_duplicates_repeat_the_last_n_unique_pairs_content_exactly():
    result = E.select_extrinsic_subset(_pool(50), unique_size=20, duplicate_count=5)
    unique = [p for p in result["pairs"] if p["duplicate_of"] is None]
    dupes = [p for p in result["pairs"] if p["duplicate_of"] is not None]
    assert len(dupes) == 5
    originals_by_n = {p["n"]: p for p in unique}
    for dupe in dupes:
        original = originals_by_n[dupe["duplicate_of"]]
        assert dupe["from_id"] == original["from_id"]
        assert dupe["to_id"] == original["to_id"]
        assert dupe["from_name"] == original["from_name"]
        assert dupe["n"] != original["n"]


def test_n_values_are_unique_and_sequential_from_one():
    result = E.select_extrinsic_subset(_pool(50), unique_size=20, duplicate_count=5)
    ns = [p["n"] for p in result["pairs"]]
    assert ns == list(range(1, 26))


def test_selection_is_deterministic():
    pool = _pool(50)
    a = E.select_extrinsic_subset(pool, unique_size=20, duplicate_count=5)
    b = E.select_extrinsic_subset(pool, unique_size=20, duplicate_count=5)
    assert a == b


def test_raises_when_pool_too_small():
    with pytest.raises(ValueError, match="pool has 10 pairs, need 20"):
        E.select_extrinsic_subset(_pool(10), unique_size=20, duplicate_count=5)


def test_fingerprint_changes_when_a_pair_changes():
    pool = _pool(50)
    a = E.select_extrinsic_subset(pool, unique_size=20, duplicate_count=5)
    pool2 = _pool(50)
    pool2[0]["to_id"] = "entities_v2/different"
    b = E.select_extrinsic_subset(pool2, unique_size=20, duplicate_count=5)
    assert E.fingerprint_extrinsic_set(a["pairs"]) != E.fingerprint_extrinsic_set(b["pairs"])
