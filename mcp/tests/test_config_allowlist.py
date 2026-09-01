"""The Qdrant allowlist ships unchanged and extends only from the environment.

`server.py` runs an isolation sanity check at every start that probes EVERY
allowlisted collection and ``raise SystemExit`` on any it cannot read, with no
bypass. Consumers hold only the shipped ``technology`` collection, so any extra
name compiled into the default dict would hard-fail every install worldwide.

These tests pin both halves: the default is untouched, and a deployment can add
its own collections without a code change.
"""

import importlib

import config
import pytest


@pytest.fixture
def reloaded(monkeypatch):
    """Reload config under a given EXTRA_QDRANT_COLLECTIONS value.

    config reads the environment at import time, so nothing short of a reload
    exercises the real code path.
    """

    def _load(value):
        if value is None:
            monkeypatch.delenv("EXTRA_QDRANT_COLLECTIONS", raising=False)
        else:
            monkeypatch.setenv("EXTRA_QDRANT_COLLECTIONS", value)
        return importlib.reload(config)

    yield _load
    monkeypatch.delenv("EXTRA_QDRANT_COLLECTIONS", raising=False)
    importlib.reload(config)


SHIPPED = {"technology": "technology"}


def test_the_default_allowlist_is_exactly_the_shipped_collection(reloaded):
    """Byte-identical with no env set. Every consumer install depends on this."""
    assert reloaded(None).ALLOWED_QDRANT_COLLECTIONS == SHIPPED


def test_an_empty_value_changes_nothing(reloaded):
    assert reloaded("").ALLOWED_QDRANT_COLLECTIONS == SHIPPED
    assert reloaded("   ").ALLOWED_QDRANT_COLLECTIONS == SHIPPED


def test_a_set_env_adds_exactly_the_pair(reloaded):
    mod = reloaded("technology_v2:technology")
    assert mod.ALLOWED_QDRANT_COLLECTIONS == {
        "technology": "technology",
        "technology_v2": "technology",
    }


def test_several_pairs_are_accepted(reloaded):
    mod = reloaded("a:one, b:two")
    assert mod.ALLOWED_QDRANT_COLLECTIONS["a"] == "one"
    assert mod.ALLOWED_QDRANT_COLLECTIONS["b"] == "two"


def test_whitespace_around_a_pair_is_ignored(reloaded):
    assert (
        reloaded("  technology_v2 : technology  ").ALLOWED_QDRANT_COLLECTIONS["technology_v2"]
        == "technology"
    )


@pytest.mark.parametrize("bad", ["technology_v2", "technology_v2:", ":technology", ":"])
def test_a_malformed_entry_raises_instead_of_being_dropped(bad):
    """A typo must fail loudly at load.

    Silently skipping a malformed pair would leave the collection unallowed,
    and the operator would meet it much later as an unexplained empty search.
    """
    with pytest.raises(ValueError, match="EXTRA_QDRANT_COLLECTIONS"):
        config._parse_extra_collections(bad)


def test_the_parser_returns_empty_for_nothing():
    assert config._parse_extra_collections(None) == {}
    assert config._parse_extra_collections("") == {}
