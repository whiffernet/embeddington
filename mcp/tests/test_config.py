"""Tests for the allowlist config — the single source of truth for which
Qdrant collections are reachable and which encoder each uses."""

import importlib

import config
import pytest


def test_allowlist_has_technology_collection():
    assert set(config.ALLOWED_QDRANT_COLLECTIONS) == {"technology"}


def test_allowlist_maps_collection_to_embed_index():
    # /embed routes by index name == collection name today (identity map).
    assert config.ALLOWED_QDRANT_COLLECTIONS["technology"] == "technology"


def test_default_collection_is_technology():
    assert config.DEFAULT_QDRANT_COLLECTION == "technology"


def test_default_embed_index_derived_from_default_collection():
    assert (
        config.DEFAULT_EMBED_INDEX
        == config.ALLOWED_QDRANT_COLLECTIONS[config.DEFAULT_QDRANT_COLLECTION]
    )


def test_invalid_default_collection_env_raises(monkeypatch):
    monkeypatch.setenv("DEFAULT_QDRANT_COLLECTION", "not_a_real_collection")
    with pytest.raises(ValueError, match="not in ALLOWED_QDRANT_COLLECTIONS"):
        importlib.reload(config)
    # restore the module to its real state for other tests
    monkeypatch.delenv("DEFAULT_QDRANT_COLLECTION", raising=False)
    importlib.reload(config)


def test_max_response_tokens_default_and_env(monkeypatch):
    monkeypatch.delenv("EMBEDDINGTON_MAX_RESPONSE_TOKENS", raising=False)
    importlib.reload(config)
    assert config.MAX_RESPONSE_TOKENS == 12000
    monkeypatch.setenv("EMBEDDINGTON_MAX_RESPONSE_TOKENS", "9000")
    importlib.reload(config)
    assert config.MAX_RESPONSE_TOKENS == 9000
    # restore the module to its real state for other tests
    monkeypatch.delenv("EMBEDDINGTON_MAX_RESPONSE_TOKENS", raising=False)
    importlib.reload(config)


# --- optional QDRANT_API_KEY (#66) ----------------------------------------
# Default MUST be "no credential": that is what every install running the
# bundled compose file uses, and it must never become required.


def test_qdrant_api_key_defaults_to_none(monkeypatch):
    monkeypatch.delenv("QDRANT_API_KEY", raising=False)
    importlib.reload(config)
    assert config.QDRANT_API_KEY is None


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
def test_qdrant_api_key_blank_is_treated_as_unset(monkeypatch, blank):
    """A user who uncomments `QDRANT_API_KEY=` gets "" from dotenv, not None."""
    monkeypatch.setenv("QDRANT_API_KEY", blank)
    importlib.reload(config)
    assert config.QDRANT_API_KEY is None


def test_qdrant_api_key_is_stripped(monkeypatch):
    monkeypatch.setenv("QDRANT_API_KEY", "  a-key\n")
    importlib.reload(config)
    assert config.QDRANT_API_KEY == "a-key"


# --- KG schema knob (Track 2 cutover switch, spec §9.2) ---------------------


def test_kg_schema_defaults_to_v2(monkeypatch):
    monkeypatch.delenv("EMBEDDINGTON_KG_SCHEMA", raising=False)
    importlib.reload(config)
    assert config.KG_SCHEMA == "v2"
    assert config.kg_collections() == {
        "entities": "entities_v2",
        "relationships": "relationships_v2",
        "graph": "servicenow_graph_v2",
        "search_view": "entities_v2_search",
    }


def test_kg_schema_v3_resolves_the_four_v3_names(monkeypatch):
    monkeypatch.setenv("EMBEDDINGTON_KG_SCHEMA", "v3")
    importlib.reload(config)
    try:
        assert config.KG_SCHEMA == "v3"
        assert config.kg_collections() == {
            "entities": "entities_v3",
            "relationships": "relationships_v3",
            "graph": "servicenow_graph_v3",
            "search_view": "entities_v3_search",
        }
    finally:
        # restore the module to its real state for other tests
        monkeypatch.delenv("EMBEDDINGTON_KG_SCHEMA", raising=False)
        importlib.reload(config)


def test_kg_schema_unknown_value_raises_naming_the_env_var(monkeypatch):
    monkeypatch.setenv("EMBEDDINGTON_KG_SCHEMA", "v9")
    with pytest.raises(ValueError, match="EMBEDDINGTON_KG_SCHEMA"):
        importlib.reload(config)
    # restore the module to its real state for other tests
    monkeypatch.delenv("EMBEDDINGTON_KG_SCHEMA", raising=False)
    importlib.reload(config)


def test_kg_collections_returns_a_fresh_dict_each_call(monkeypatch):
    """Callers must not be able to corrupt the schema table by mutating what
    kg_collections() returns."""
    monkeypatch.delenv("EMBEDDINGTON_KG_SCHEMA", raising=False)
    importlib.reload(config)
    first = config.kg_collections()
    first["entities"] = "tampered"
    assert config.kg_collections()["entities"] == "entities_v2"
