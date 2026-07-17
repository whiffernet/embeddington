"""Tier-1 (pure, no I/O) tests for the budget/selection module."""

from budget import (
    Concept,
    allocate_budget,
    coalesced_confidence,
    estimate_tokens,
    group_concepts,
    normalize_name,
    select_edges,
    trim_to_ceiling,
)


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


def test_group_concepts_ranks_variants_by_degree_not_encounter_order():
    """variants[0] must be the highest-degree variant, even when a low-degree
    variant is encountered first via an earlier hint (cross-hint merge)."""
    seeded = [
        (0, _e("low", "CMDB", "Feature", degree=5)),
        (1, _e("high", "CMDB", "Product", degree=500)),
    ]
    concepts = group_concepts(seeded)
    assert len(concepts) == 1
    assert concepts[0].variants[0]["degree"] == 500


def test_group_concepts_dedup_disable_hook_is_env_gated(monkeypatch):
    """BUDGET_DISABLE_DEDUP=1 keys on entity id — every entity its own concept.

    The hook must ONLY activate under the env var: same-name variants that
    normally merge into one concept split into N when it is set, and merge
    again when it is cleared. Ids are non-prefixing so the prefix-merge rule
    can't muddy the assertion.
    """
    seeded = [
        (0, _e("alpha", "Process Mining", "Feature", 300)),
        (0, _e("beta", "Process Mining", "Product", 200)),
    ]
    # Default (env unset by conftest): same normalized name -> one concept.
    monkeypatch.delenv("BUDGET_DISABLE_DEDUP", raising=False)
    assert len(group_concepts(seeded)) == 1

    # Hook active: each entity is its own concept, one variant apiece.
    monkeypatch.setenv("BUDGET_DISABLE_DEDUP", "1")
    concepts = group_concepts(seeded)
    assert len(concepts) == 2
    assert all(len(c.variants) == 1 for c in concepts)

    # And it deactivates cleanly when the flag is anything but "1".
    monkeypatch.setenv("BUDGET_DISABLE_DEDUP", "0")
    assert len(group_concepts(seeded)) == 1


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


def _edge(eid: str, predicate: str, confidence=None) -> dict:
    return {
        "id": eid,
        "source": "entities_v2/a",
        "target": f"entities_v2/{eid}",
        "predicate": predicate,
        "confidence": confidence,
        "extraction_type": "explicit",
        "releases": None,
        "source_document": "d",
        "source_quote": "q",
    }


def test_estimate_tokens_is_ceil_len_over_3():
    obj = {"a": "xxxx"}  # compact json: {"a":"xxxx"} = 12 chars → 4 tokens
    assert estimate_tokens(obj) == 4


def test_coalesced_confidence_null_is_midtier():
    assert coalesced_confidence(_edge("e1", "CONTAINS", None)) == 0.5
    assert coalesced_confidence(_edge("e2", "CONTAINS", 0.9)) == 0.9


def test_select_edges_keeps_minority_predicate_over_bulk_confidence():
    # The recall critic's killer: 50 CONTAINS@0.96 vs 1 INGESTS_FROM@0.78.
    edges = [_edge(f"c{i}", "CONTAINS", 0.96) for i in range(50)]
    edges.append(_edge("answer", "INGESTS_FROM", 0.78))
    kept = select_edges(edges, slots=10)
    assert any(e["predicate"] == "INGESTS_FROM" for e in kept)


def test_select_edges_null_confidence_class_not_starved():
    edges = [_edge(f"c{i}", "CONTAINS", 0.9) for i in range(20)]
    edges.append(_edge("structural", "HAS_FIELD", None))  # bulk-loaded, unscored
    kept = select_edges(edges, slots=5)
    assert any(e["predicate"] == "HAS_FIELD" for e in kept)


def test_select_edges_respects_slots_and_is_deterministic():
    edges = [_edge(f"e{i}", "CONTAINS", 0.9) for i in range(10)]
    a = select_edges(list(edges), slots=4)
    b = select_edges(list(reversed(edges)), slots=4)
    assert len(a) == 4 and [e["id"] for e in a] == [e["id"] for e in b]


def _match(concept: str, n_edges: int) -> dict:
    edges = [_edge(f"{concept}-{i}", "CONTAINS", 0.9 - i * 0.01) for i in range(n_edges)]
    return {
        "concept": concept,
        "variants": [],
        "nodes": [],
        "edges": edges,
        "truncation": {"truncated": False, "available": n_edges, "returned": n_edges},
        "suggest": None,
        "error": None,
    }


def _envelope(matches: list[dict], n_chunks: int = 2) -> dict:
    chunks = [
        {
            "id": str(i),
            "score": 0.9 - i * 0.1,
            "text": "x" * 500,
            "source": "s",
            "metadata": {},
        }
        for i in range(n_chunks)
    ]
    return {
        "vector_chunks": chunks,
        "kg_matches": matches,
        "errors": {},
        "budget": {
            "edge_budget": 60,
            "returned": sum(len(m["edges"]) for m in matches),
            "truncated": False,
        },
        "warnings": [],
    }


def test_trim_noop_when_under_ceiling():
    env = _envelope([_match("a", 3)])
    before = estimate_tokens(env)
    out = trim_to_ceiling(env, max_tokens=before + 100)
    assert len(out["kg_matches"][0]["edges"]) == 3
    assert out["budget"]["truncated"] is False


def test_trim_drops_from_largest_match_first():
    env = _envelope([_match("big", 20), _match("small", 4)])
    target = estimate_tokens(env) - 40  # force a few drops
    out = trim_to_ceiling(env, max_tokens=target)
    assert len(out["kg_matches"][1]["edges"]) == 4  # small untouched
    assert len(out["kg_matches"][0]["edges"]) < 20
    assert out["kg_matches"][0]["truncation"]["truncated"] is True


def test_trim_never_breaks_the_top3_floor():
    env = _envelope([_match("a", 6), _match("b", 6)], n_chunks=1)
    out = trim_to_ceiling(env, max_tokens=1)  # impossible ceiling
    for m in out["kg_matches"]:
        assert len(m["edges"]) >= 3  # hinted-concept floor holds
    assert out["budget"]["truncated"] is True
    assert out["warnings"]  # over-ceiling admitted, not silent


def test_trim_touches_chunks_only_after_edges_and_keeps_one():
    env = _envelope([_match("a", 4)], n_chunks=3)
    out = trim_to_ceiling(env, max_tokens=1)
    assert len(out["vector_chunks"]) == 1  # never zero
