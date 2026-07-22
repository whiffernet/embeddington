"""Unit tests for the binding guard in ontology_snapshot.py (spec §4/M1).

No live stack: a fake db handle returns canned AQL results, same pattern as
test_ontology_metrics.py. This is deliberately a separate module from
test_ontology_live.py, whose pytestmark skips everything unless
EMBEDDINGTON_BATTERY=1 — the guard must be exercised on every run, not only
when a battery stack happens to be up.
"""

import ontology_frozen as F
import ontology_snapshot as S
import pytest


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class FakeAQL:
    """Returns queued results in order, one per execute() call."""

    def __init__(self, results):
        self._results = list(results)
        self.queries = []

    def execute(self, query, bind_vars=None):
        self.queries.append((query, bind_vars))
        return FakeCursor(self._results.pop(0))


class FakeDB:
    def __init__(self, results):
        self.aql = FakeAQL(results)


def _matching_db():
    return FakeDB([[{"entities": F.EXPECTED_ENTITIES, "edges": F.EXPECTED_EDGES}]])


def test_assert_binding_passes_when_counts_match_the_frozen_binding():
    S.assert_binding(_matching_db())  # must not raise


def test_assert_binding_raises_on_entity_drift():
    db = FakeDB([[{"entities": F.EXPECTED_ENTITIES - 1, "edges": F.EXPECTED_EDGES}]])
    with pytest.raises(S.BindingMismatchError):
        S.assert_binding(db)


def test_assert_binding_raises_on_edge_drift():
    db = FakeDB([[{"entities": F.EXPECTED_ENTITIES, "edges": F.EXPECTED_EDGES - 1}]])
    with pytest.raises(S.BindingMismatchError):
        S.assert_binding(db)


def test_assert_binding_message_names_expected_and_actual_counts():
    """The message must make the likely cause (wrong stack) obvious."""
    db = FakeDB([[{"entities": 1, "edges": 2}]])
    with pytest.raises(S.BindingMismatchError) as exc_info:
        S.assert_binding(db)
    message = str(exc_info.value)
    assert str(F.EXPECTED_ENTITIES) in message
    assert str(F.EXPECTED_EDGES) in message
    assert "1" in message
    assert "2" in message
    assert "production" in message.lower()


def test_collect_hard_fails_before_computing_any_metric():
    """A wrong-stack collect() must raise, not silently proceed to collect."""
    db = FakeDB([[{"entities": 0, "edges": 0}]])
    with pytest.raises(S.BindingMismatchError):
        S.collect(db)
    # Only the binding-check query ran — nothing else was queued/consumed.
    assert len(db.aql.queries) == 1
