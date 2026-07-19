"""Pin the battery query cohorts against accidental edits.

QUERIES (the fixed-11 acceptance battery) must never change without a
deliberate, reviewed decision — spec §7 scores it as a frozen artifact.
IDENTIFIER_QUERIES (spec §3.4) is a second, separately-scored cohort; this
pins it to the four corpus-verified queries the controller confirmed.
"""

from battery_queries import IDENTIFIER_QUERIES, QUERIES

FIXED_NAMES = [
    "case1_realistic_3hint",
    "case2_minimal",
    "hub_cmdb_rel_ci",
    "hub_process_mining",
    "hub_discovery",
    "hub_cmdb",
    "hub_incident",
    "hub_predictive_intelligence",
    "control_no_hints_snake",
    "control_predicate_filter",
    "control_multifacet_license",
]

IDENTIFIER_CONTRACT = {
    "id_disc_plugin": "What does the com.snc.discovery plugin activate?",
    "id_mim_plugin": (
        "What does the com.snc.incident.mim plugin provide for major incident management?"
    ),
    "id_pm_project": "What is the pm_project table used for?",
    "id_sc_cat_item": "How is the sc_cat_item table related to the service catalog?",
}


def test_queries_frozen_names():
    """The fixed-11 cohort's names (and order) must never drift."""
    assert [q["name"] for q in QUERIES] == FIXED_NAMES


def test_identifier_queries_match_contract():
    """IDENTIFIER_QUERIES pins the four corpus-verified id_* queries."""
    assert [q["name"] for q in IDENTIFIER_QUERIES] == list(IDENTIFIER_CONTRACT)
    for q in IDENTIFIER_QUERIES:
        assert q["query"] == IDENTIFIER_CONTRACT[q["name"]]
        assert q["entity_hints"] is None
        assert q["top_k"] == 5
        assert q["edge_budget"] == 40
        assert q["predicates"] is None


def test_identifier_queries_never_blended_into_fixed():
    """The two cohorts must stay disjoint and QUERIES itself unaugmented."""
    assert set(q["name"] for q in QUERIES).isdisjoint(q["name"] for q in IDENTIFIER_QUERIES)
    assert len(QUERIES) == 11
