import pytest

from embeddington.apply import schema_names


def test_absent_kg_schema_resolves_to_v2_names():
    assert schema_names.resolve_schema_names(None) == {
        "entities": "entities_v2",
        "relationships": "relationships_v2",
        "graph": "servicenow_graph_v2",
        "search_view": "entities_v2_search",
    }


def test_v2_kg_schema_resolves_identically_to_absent():
    assert schema_names.resolve_schema_names("v2") == schema_names.resolve_schema_names(None)


def test_v3_kg_schema_resolves_to_v3_names():
    assert schema_names.resolve_schema_names("v3") == {
        "entities": "entities_v3",
        "relationships": "relationships_v3",
        "graph": "servicenow_graph_v3",
        "search_view": "entities_v3_search",
    }


def test_unknown_kg_schema_raises():
    with pytest.raises(ValueError, match="unknown kg_schema"):
        schema_names.resolve_schema_names("v99")


def test_resolve_returns_a_fresh_dict_each_time():
    """A caller mutating its resolved names must never corrupt the shared table."""
    first = schema_names.resolve_schema_names("v3")
    first["entities"] = "mutated"
    assert schema_names.resolve_schema_names("v3")["entities"] == "entities_v3"


def test_all_collection_names_covers_v2_and_v3_only():
    assert schema_names.all_collection_names() == {
        "entities_v2",
        "relationships_v2",
        "entities_v3",
        "relationships_v3",
    }
