"""Cross-repo contract: a re-rooted manifest's ``kg_schema`` field, end to end.

The internal publisher (a separate repo) re-roots the diff chain onto a new baseline
during the KG v3 cutover; ``kg_cutover.py`` there composes the re-rooted manifest's
``--baseline-entry`` JSON from the NEW baseline's own manifest, so the ``kg_schema``
field a Task-2-upstream baseline stamps travels onto the re-rooted ``diffs`` manifest
unchanged. That composer lives only in the internal publisher repo, not here, so this
test exercises the PUBLIC contract with a fixture manifest that mirrors exactly the
shape a re-rooted release publishes: this consumer package's own
``embeddington.apply.cursor.plan_update`` (the client-side half of the contract) must
validate it and ``embeddington.apply.schema_names.resolve_schema_names`` must resolve
the right physical names from the field it carries.
"""

from embeddington.apply import cursor, schema_names


def _rerooted_manifest(baseline_kg_schema=None):
    """A manifest shaped exactly like a re-rooted release: one baseline, no diffs yet.

    Args:
        baseline_kg_schema: Value for the baseline entry's ``kg_schema`` field, or
            None to omit the field entirely (the pre-cutover shape).
    """
    baseline = {
        "tag": "baseline-2026-09-reroot",
        "head_sha": "reroot-head-sha",
        "points": 150_000,
        "entities": 40_000,
        "edges": 90_000,
        "assets": {"qdrant": "technology.snapshot.zst", "arango": "arango-dump.tar.zst"},
        "sha256": {"qdrant": "qsha", "arango": "asha"},
    }
    if baseline_kg_schema is not None:
        baseline["kg_schema"] = baseline_kg_schema
    return {
        "schema_version": "4.0.0",
        "baselines": [baseline],
        "diffs": [],
    }


def test_a_4_0_0_client_validates_a_rerooted_v3_manifest_and_resolves_v3_names():
    manifest = _rerooted_manifest(baseline_kg_schema="v3")

    plan = cursor.plan_update(None, manifest)  # default supported_major == SUPPORTED_SCHEMA_MAJOR

    assert plan.mode == "baseline"
    names = schema_names.resolve_schema_names(plan.baseline.get("kg_schema"))
    assert names == {
        "entities": "entities_v3",
        "relationships": "relationships_v3",
        "graph": "servicenow_graph_v3",
        "search_view": "entities_v3_search",
    }


def test_the_same_manifest_without_kg_schema_resolves_v2_names():
    """A baseline entry without the field (every manifest published before Task 2's
    upstream stamping existed) must resolve identically to v2 -- byte-for-byte the
    same names a pre-cutover client already writes to."""
    manifest = _rerooted_manifest(baseline_kg_schema=None)

    plan = cursor.plan_update(None, manifest)

    assert plan.mode == "baseline"
    assert "kg_schema" not in plan.baseline
    names = schema_names.resolve_schema_names(plan.baseline.get("kg_schema"))
    assert names == {
        "entities": "entities_v2",
        "relationships": "relationships_v2",
        "graph": "servicenow_graph_v2",
        "search_view": "entities_v2_search",
    }


def test_a_pre_cutover_client_refuses_the_rerooted_chain_instead_of_corrupting_it():
    """R6: an install that has NOT taken this release (still SUPPORTED_SCHEMA_MAJOR
    3) must refuse the re-rooted major-4 chain outright, rather than applying it
    against its v2 collections."""
    from embeddington.errors import SchemaVersionError

    manifest = _rerooted_manifest(baseline_kg_schema="v3")
    try:
        cursor.plan_update(None, manifest, supported_major=3)
    except SchemaVersionError:
        pass
    else:
        raise AssertionError("a pre-cutover client must refuse a major-4 manifest")
