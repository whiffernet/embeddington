"""Tests for the lazy per-collection client registry in server.py.

These exercise the REAL _get_embed/_get_qdrant getters (no mocking) to prove
the cache-or-create behavior: one client instance per key, reused on repeat
calls. Construction opens no network connection, so this is offline-safe.
"""

import server as srv


def _reset_registries(monkeypatch):
    monkeypatch.setattr(srv, "_embed_clients", {})
    monkeypatch.setattr(srv, "_qdrant_clients", {})


def test_get_embed_caches_one_client_per_index(monkeypatch):
    _reset_registries(monkeypatch)
    a = srv._get_embed("technology")
    b = srv._get_embed("technology")
    assert a is b, "same index must return the same cached EmbeddingClient"
    assert a.index == "technology"


def test_get_embed_distinct_clients_per_index(monkeypatch):
    _reset_registries(monkeypatch)
    tech = srv._get_embed("technology")
    other = srv._get_embed("other_index")
    assert tech is not other
    assert tech.index == "technology"
    assert other.index == "other_index"


def test_get_qdrant_caches_one_client_per_collection(monkeypatch):
    _reset_registries(monkeypatch)
    a = srv._get_qdrant("technology")
    b = srv._get_qdrant("technology")
    assert a is b, "same collection must return the same cached QdrantSearchClient"
    assert a.collection == "technology"


def test_get_qdrant_distinct_clients_per_collection(monkeypatch):
    _reset_registries(monkeypatch)
    tech = srv._get_qdrant("technology")
    other = srv._get_qdrant("other_collection")
    assert tech is not other
    assert other.collection == "other_collection"


def test_getters_default_to_config_defaults(monkeypatch):
    _reset_registries(monkeypatch)
    e = srv._get_embed()
    q = srv._get_qdrant()
    assert e.index == srv.config.DEFAULT_EMBED_INDEX
    assert q.collection == srv.config.DEFAULT_QDRANT_COLLECTION
