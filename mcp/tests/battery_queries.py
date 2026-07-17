"""Fixed Tier-2 query battery (spec §7). Case 1/2 are verbatim from the
2026-07-16 bakeoff issue report and are the acceptance anchors."""

CASE_1 = {
    "name": "case1_realistic_3hint",
    "query": (
        "Design a solution using Process Mining to detect and remediate "
        "incident-resolution bottlenecks, feeding recommendations back into "
        "Predictive Intelligence. Specify the data source Process Mining "
        "ingests from, the OOB output surfaces, the plugin dependency chain "
        "to Predictive Intelligence, and the license implication of pairing "
        "Process Mining with ITSM Pro+."
    ),
    "entity_hints": ["Process Mining", "Predictive Intelligence", "ITSM Pro+"],
    "top_k": 10,
    "edge_budget": 60,
    "predicates": None,
}
CASE_2 = {
    "name": "case2_minimal",
    "query": "What is the cmdb_rel_ci table used for?",
    "entity_hints": ["cmdb_rel_ci"],
    "top_k": 3,
    "edge_budget": 60,
    "predicates": None,
}
HUBS = [
    {
        "name": f"hub_{h.lower().replace(' ', '_')}",
        "query": f"Explain {h} in ServiceNow.",
        "entity_hints": [h],
        "top_k": 5,
        "edge_budget": 60,
        "predicates": None,
    }
    for h in [
        "cmdb_rel_ci",
        "Process Mining",
        "Discovery",
        "CMDB",
        "Incident",
        "Predictive Intelligence",
    ]
]
CONTROLS = [
    {
        "name": "control_no_hints_snake",
        "query": "What is the sc_req_item table?",
        "entity_hints": None,
        "top_k": 3,
        "edge_budget": 60,
        "predicates": None,
    },
    {
        "name": "control_predicate_filter",
        "query": "What roles does Discovery require?",
        "entity_hints": ["Discovery"],
        "top_k": 3,
        "edge_budget": 30,
        "predicates": ["REQUIRES_ROLE"],
    },
    {
        "name": "control_multifacet_license",
        "query": "What is the license implication of Process Mining?",
        "entity_hints": ["Process Mining"],
        "top_k": 3,
        "edge_budget": 40,
        "predicates": None,
    },
]
QUERIES = [CASE_1, CASE_2, *HUBS, *CONTROLS]
