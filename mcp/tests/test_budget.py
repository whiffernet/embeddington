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


# --- orphan-node pruning on ceiling trim (issue #28 edge-count inversion) ---
# _kg_side materializes match["nodes"] from the endpoints of the PRE-trim
# selected edges; the ceiling trim then pops edges. Without pruning the nodes
# those popped edges orphaned, the response carried entities with no surviving
# edge (contract violation) AND their ~tokens held the response over ceiling,
# flooring concepts toward the 3-edge floor — so a larger edge_budget delivered
# FEWER edges (the eb=120 inversion in the battery sweep).


def _match_with_distinct_nodes(concept: str, n_edges: int) -> dict:
    """Match whose n_edges each point to a distinct target node, plus those
    nodes — mirrors the enrich `_kg_side` shape (nodes = selected-edge
    endpoints) that exposed the orphan-node trim bug. Node payloads are padded
    to a realistic size so dropping edges frees a meaningful token amount."""
    edges = [
        {
            "id": f"{concept}-{i}",
            "source": f"entities_v2/{concept}",
            "target": f"entities_v2/{concept}-n{i}",
            "predicate": "CONTAINS",
            "confidence": 0.9 - i * 0.001,
            "extraction_type": "explicit",
            "releases": None,
            "source_document": "IT Service Management",
            "source_quote": "q" * 200,
        }
        for i in range(n_edges)
    ]
    nodes = [
        {
            "id": f"entities_v2/{concept}-n{i}",
            "name": f"node {i} " + "z" * 120,
            "type": "Feature",
            "releases": ["zurich"],
        }
        for i in range(n_edges)
    ]
    return {
        "concept": concept,
        "variants": [],
        "nodes": nodes,
        "edges": edges,
        "truncation": {"truncated": False, "available": n_edges, "returned": n_edges},
        "suggest": None,
        "error": None,
    }


def _orphan_node_ids(match: dict) -> list[str]:
    """Node ids in the match that are NOT an endpoint of any surviving edge."""
    endpoints = {e["source"] for e in match["edges"]} | {e["target"] for e in match["edges"]}
    return [n["id"] for n in match["nodes"] if n["id"] not in endpoints]


def test_trim_prunes_orphan_nodes_and_reuses_freed_tokens():
    """20 edges to 20 distinct nodes, ceiling forcing a heavy trim: after trim
    no node is an orphan, and delivered edges stay well above the floor because
    the freed node-tokens are reclaimed for edges instead of holding the
    response floored (the pre-fix failure)."""
    floor = 3
    env = _envelope([_match_with_distinct_nodes("hub", 20)], n_chunks=1)
    ceiling = estimate_tokens(env) * 6 // 10  # room above floor once orphans go
    out = trim_to_ceiling(env, max_tokens=ceiling, floor=floor)
    m = out["kg_matches"][0]
    assert _orphan_node_ids(m) == []  # nodes-are-edge-endpoints contract holds
    assert len(m["nodes"]) == len(m["edges"])  # 1:1 here — every node still cited
    assert len(m["edges"]) > floor  # freed node-tokens reused, not floored
    assert estimate_tokens(out) <= ceiling


def _redge(eid, pred, conf=None, quote="q") -> dict:
    return {"id": eid, "predicate": pred, "confidence": conf, "source_quote": quote}


class TestSelectEdgesRelevance:
    def test_none_relevance_is_byte_identical_to_legacy(self):
        edges = [_redge("a", "P1", 0.9), _redge("b", "P2", 0.8), _redge("c", "P1", 0.7)]
        legacy = select_edges(edges, 2)
        assert select_edges(edges, 2, relevance=None) == legacy
        assert select_edges(edges, 2, relevance=None, diversity_quota=1) == legacy

    def test_quota_pick_ranks_by_relevance_not_confidence(self):
        # High-confidence edge loses to high-relevance edge in the quota phase.
        edges = [_redge("hi_conf", "P1", 0.99), _redge("hi_rel", "P1", 0.10)]
        rel = {"hi_rel": 0.95, "hi_conf": 0.05}
        kept = select_edges(edges, 1, relevance=rel, diversity_quota=1)
        assert [e["id"] for e in kept] == ["hi_rel"]

    def test_quota_picks_come_first_in_rank_order_then_fill(self):
        # 3 predicates, quota=2: pins the FULL ordered output — quota picks
        # (best edge per predicate, in rank order across predicates) first,
        # fill picks (remaining edges, in rank order) after. This is the
        # property the ceiling trim's tail-pop depends on: it must sacrifice
        # diversity last, so quota picks can never trail fill picks.
        edges = [
            _redge("p1a", "P1"),
            _redge("p1b", "P1"),
            _redge("p2a", "P2"),
            _redge("p3a", "P3"),
        ]
        rel = {"p1a": 0.9, "p2a": 0.8, "p1b": 0.5, "p3a": 0.3}
        kept = select_edges(edges, 4, relevance=rel, diversity_quota=2)
        assert [e["id"] for e in kept] == ["p1a", "p2a", "p1b", "p3a"]

    def test_quota_preserves_minority_predicate(self):
        # 3 highly-relevant P1 edges would fill slots=3; quota must save P2's best.
        edges = [
            _redge("p1a", "P1", 0.9),
            _redge("p1b", "P1", 0.9),
            _redge("p1c", "P1", 0.9),
            _redge("p2a", "P2", 0.1),
        ]
        rel = {"p1a": 0.9, "p1b": 0.8, "p1c": 0.7, "p2a": 0.2}
        kept = select_edges(edges, 3, relevance=rel, diversity_quota=2)
        ids = {e["id"] for e in kept}
        assert "p2a" in ids  # survived via quota
        assert "p1a" in ids  # best edge overall also present

    def test_default_quota_is_fraction_of_slots_min_one(self):
        # slots=8 -> quota = max(1, round(DIVERSITY_QUOTA_FRACTION * slots))
        #          = max(1, round(0.40 * 8)) = max(1, round(3.2)) = 3.
        # 4 predicates present (P1 x8 high-relevance, p2a/p3a/p4a descending):
        # pass 1 (quota) walks all edges in relevance order and takes the
        # first (highest-relevance) edge of each new predicate — P1's best
        # edge, then p2a, then p3a — hitting the quota=3 cap before p4a is
        # ever reached, so p4a never wins a quota slot.
        # Pass 2 (fill) resumes in the same relevance order for the
        # remaining 8-3=5 slots: the 7 still-unpicked P1 edges (relevance
        # 0.9) all outrank p4a (relevance 0.1), so fill drains 5 of them
        # before reaching p4a — p4a is excluded from the result entirely.
        edges = [_redge(f"p1{i}", "P1", 0.5) for i in range(8)]
        edges += [_redge("p2a", "P2", 0.5), _redge("p3a", "P3", 0.5), _redge("p4a", "P4", 0.5)]
        rel = {e["id"]: 0.9 if e["predicate"] == "P1" else 0.1 for e in edges}
        rel["p2a"] = 0.3  # p2 ranks above p3 ranks above p4 (p4a stays at 0.1)
        rel["p3a"] = 0.2
        kept = select_edges(edges, 8, relevance=rel)
        ids = {e["id"] for e in kept}
        assert "p2a" in ids  # second quota pick
        assert "p3a" in ids  # third quota pick — quota now exhausted at 3
        assert "p4a" not in ids  # never wins quota or fill (7 P1 edges rank above it)
        assert sum(1 for i in ids if i.startswith("p1")) == 6  # 1 quota P1 + 5 fill P1

    def test_unscored_edges_eligible_not_sunk(self):
        # Unscored edge: no relevance entry. It must (a) win a quota slot for its
        # predicate, and (b) rank by confidence among unscored in the fill.
        edges = [_redge("scored", "P1", 0.1), _redge("unscored", "P2", 0.9, quote=None)]
        rel = {"scored": 0.5}
        kept = select_edges(edges, 2, relevance=rel, diversity_quota=1)
        assert {e["id"] for e in kept} == {"scored", "unscored"}

    def test_scored_zero_relevance_still_outranks_unscored_in_fill(self):
        edges = [_redge("z", "P1", 0.1), _redge("u", "P1", 0.99, quote=None)]
        rel = {"z": 0.0}
        kept = select_edges(edges, 1, relevance=rel, diversity_quota=0)
        assert [e["id"] for e in kept] == ["z"]

    def test_grounding_fields_never_stripped(self):
        edges = [_redge("a", "P1", 0.9)]
        edges[0].update(
            {"source_document": "ITSM", "extraction_type": "explicit", "releases": ["x"]}
        )
        kept = select_edges(edges, 1, relevance={"a": 0.5})
        assert kept[0]["source_document"] == "ITSM"
        assert kept[0]["releases"] == ["x"]

    def test_deterministic_on_ties(self):
        edges = [_redge("b", "P1", 0.5), _redge("a", "P1", 0.5)]
        rel = {"a": 0.5, "b": 0.5}
        k1 = select_edges(edges, 1, relevance=rel)
        k2 = select_edges(list(reversed(edges)), 1, relevance=rel)
        assert [e["id"] for e in k1] == [e["id"] for e in k2] == ["a"]


def test_trim_does_not_invert_to_floor_under_ceiling_eb120_case():
    """The eb=120 inversion: 5 concepts each with 24 edges+nodes overshoot the
    12000 ceiling. Pre-fix, the trim floored every concept to 3 edges (15 total)
    while ~100 orphan nodes pinned the response ~1600-2300 tokens UNDER the
    ceiling — a larger budget delivering fewer edges. With orphan pruning the
    freed node-tokens go to edges: delivery is well above the floor and no
    concept carries an orphan node."""
    floor = 3
    matches = [_match_with_distinct_nodes(f"c{j}", 24) for j in range(5)]
    env = _envelope(matches, n_chunks=5)
    out = trim_to_ceiling(env, max_tokens=12000, floor=floor)
    total_edges = sum(len(m["edges"]) for m in out["kg_matches"])
    assert total_edges > len(matches) * floor  # NOT stuck at the 5x3=15 floor
    for m in out["kg_matches"]:
        assert _orphan_node_ids(m) == []
    assert estimate_tokens(out) <= 12000
