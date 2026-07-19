"""Unit tests for the shared gold-pool module (pure parts only)."""

import gold_pools
import pytest


def test_pool_fingerprint_is_order_independent():
    a = {"e2": {"predicate": "CONTAINS"}, "e1": {"predicate": "REQUIRES"}}
    b = {"e1": {"predicate": "REQUIRES"}, "e2": {"predicate": "CONTAINS"}}
    assert gold_pools.pool_fingerprint(a) == gold_pools.pool_fingerprint(b)
    assert len(gold_pools.pool_fingerprint(a)) == 64


def test_pool_fingerprint_changes_with_membership():
    a = {"e1": {}}
    b = {"e1": {}, "e2": {}}
    assert gold_pools.pool_fingerprint(a) != gold_pools.pool_fingerprint(b)


def test_assert_binding_passes_on_expected():
    gold_pools.assert_binding(dict(gold_pools.EXPECTED_BINDING))


def test_assert_binding_hard_fails_on_drift():
    bad = dict(gold_pools.EXPECTED_BINDING)
    bad["points"] = 1
    with pytest.raises(gold_pools.BindingError):
        gold_pools.assert_binding(bad)


def test_resolve_hints_prefers_explicit():
    q = {"query": "Explain CMDB.", "entity_hints": ["CMDB"]}
    assert gold_pools.resolve_hints(q) == ["CMDB"]


def test_build_pool_merges_and_dedups():
    class FakeArango:
        def find_entities(self, hint, limit=3):
            return [{"id": f"entities_v2/{hint}"}]

        def neighbors_stratified(self, eid, per_predicate, overall, predicates):
            return {"edges": [{"id": "shared"}, {"id": f"own-{eid}"}]}

    q = {"query": "x", "entity_hints": ["a", "b"], "predicates": None}
    pool = gold_pools.build_pool(FakeArango(), q)
    assert set(pool) == {"shared", "own-entities_v2/a", "own-entities_v2/b"}
