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
