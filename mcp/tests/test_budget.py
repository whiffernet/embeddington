"""Tier-1 (pure, no I/O) tests for the budget/selection module."""

from budget import Concept, allocate_budget, group_concepts, normalize_name


def _e(eid: str, name: str, etype: str = "Feature", degree: int = 10) -> dict:
    return {
        "id": f"entities_v2/{eid}",
        "name": name,
        "type": etype,
        "source_documents": [],
        "releases": None,
        "degree": degree,
    }


def test_normalize_name_casefold_and_punctuation():
    assert normalize_name("Process Mining") == normalize_name("process-mining")
    assert normalize_name("  CMDB_Rel_CI ") == normalize_name("cmdb rel ci")


def test_group_concepts_merges_type_variants_of_same_name():
    seeded = [
        (0, _e("feature__process_mining", "Process Mining", "Feature", 300)),
        (0, _e("product__process_mining", "Process Mining", "Product", 200)),
        (0, _e("module__process_mining", "Process Mining", "Module", 100)),
    ]
    concepts = group_concepts(seeded)
    assert len(concepts) == 1
    assert len(concepts[0].variants) == 3
    assert concepts[0].hint_index == 0


def test_group_concepts_prefix_merge_catches_fuzzy_typo_variant():
    # cmdb_rel_ci vs cmdb_rel_ciCIS — the issue doc's Case 2 pair.
    seeded = [
        (0, _e("table__cmdb_rel_ci", "cmdb_rel_ci", "Table", 500)),
        (0, _e("table__cmdb_rel_cicis", "cmdb_rel_ciCIS", "Table", 3)),
    ]
    concepts = group_concepts(seeded)
    assert len(concepts) == 1  # merged: extension of <=4 trailing chars


def test_group_concepts_does_not_merge_distinct_names():
    seeded = [
        (0, _e("a", "Incident Management")),
        (1, _e("b", "Change Management")),
    ]
    assert len(group_concepts(seeded)) == 2


def test_group_concepts_does_not_prefix_merge_beyond_threshold():
    # "Discovery" vs "Discovery pattern customization" — extension >4 chars.
    seeded = [(0, _e("a", "Discovery")), (0, _e("b", "Discovery pattern customization"))]
    assert len(group_concepts(seeded)) == 2


def test_group_concepts_dedups_same_entity_across_hints():
    ent = _e("feature__x", "X Thing")
    concepts = group_concepts([(0, ent), (1, dict(ent))])
    assert len(concepts) == 1
    assert len(concepts[0].variants) == 1
    assert concepts[0].hint_index == 0  # earliest hint wins


def test_group_concepts_unnamed_entities_stay_singletons():
    seeded = [(0, _e("a", "")), (0, _e("b", ""))]
    assert len(group_concepts(seeded)) == 2  # never bucket unparseables together


def test_group_concepts_recanonicalizes_key_on_merge():
    """Staggered chains merge fully: the concept key follows the shortest name."""
    seeded = [
        (0, _e("a", "ABCDEFGH")),
        (0, _e("b", "ABCD")),  # merges, key must become "abcd..."-normalized
        (0, _e("c", "AB")),  # must now also merge (extension 2 from ABCD)
    ]
    concepts = group_concepts(seeded)
    assert len(concepts) == 1
    assert len(concepts[0].variants) == 3


def _c(key: str, hint_index: int = 0) -> Concept:
    return Concept(key=key, variants=[_e(key, key)], hint_index=hint_index)


def test_allocate_first_hint_concept_gets_double_weight():
    concepts = [_c("primary", 0), _c("context", 1)]
    slots = allocate_budget(concepts, edge_budget=60)
    assert slots[0] == 40 and slots[1] == 20  # 2:1 weighting


def test_allocate_caps_at_max_concepts():
    concepts = [_c(f"c{i}", i) for i in range(8)]
    slots = allocate_budget(concepts, edge_budget=60)
    assert len(slots) == 5  # only first 5 budgeted; callers treat the rest as unexpanded


def test_allocate_minimum_three_per_budgeted_concept():
    concepts = [_c("a", 0), _c("b", 1), _c("c", 2)]
    slots = allocate_budget(concepts, edge_budget=10)
    assert all(s >= 3 for s in slots) and sum(slots) <= 10


def test_allocate_tiny_budget_reduces_concept_count_not_floor():
    concepts = [_c("a", 0), _c("b", 1), _c("c", 2)]
    slots = allocate_budget(concepts, edge_budget=5)
    # 5 // 3 = 1 concept gets budgeted; floor never sliced below 3
    assert slots == [5, 0, 0]


def test_allocate_leftover_goes_in_relevance_order():
    concepts = [_c("a", 0), _c("b", 1), _c("c", 2)]
    slots = allocate_budget(concepts, edge_budget=20)
    # weights 2,1,1 → raw 10,5,5; exact split, deterministic
    assert slots == [10, 5, 5]


def test_allocate_budget_below_floor_never_exceeds_budget():
    assert allocate_budget([_c("a", 0)], edge_budget=1) == [1]
    assert allocate_budget([_c("a", 0), _c("b", 1)], edge_budget=2) == [2, 0]
