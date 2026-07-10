"""Shared pytest fixtures for embeddington tests."""

import pytest


@pytest.fixture(autouse=True)
def _safe_env(monkeypatch):
    """Default safe env values so unit tests never accidentally call out.

    Tests that need real env (integration/isolation) override these
    explicitly via monkeypatch.setenv().

    Note: config.py reads env vars at module import time. monkeypatch.setenv
    only affects code that calls os.environ.get() AFTER this fixture runs —
    so tests that import config constants directly (e.g. config.ARANGO_URL)
    see whatever value was set when config first loaded, not the patched one.
    For those cases, use monkeypatch.setattr("config.X", ...).
    """
    monkeypatch.setenv("QDRANT_URL", "http://test-qdrant:6333")
    monkeypatch.setenv("ARANGO_URL", "http://test-arango:8529")
    monkeypatch.setenv("ARANGO_DATABASE", "test_knowledge_graph")
    monkeypatch.setenv("ARANGO_USER", "test-user")
    monkeypatch.setenv("ARANGO_PASSWORD", "test-pw")
    monkeypatch.setenv("EMBED_URL", "http://test-embed/embed")
