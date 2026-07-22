"""Live battery gates for the ontology snapshot (spec §4/M1).

Skips unless EMBEDDINGTON_BATTERY=1. Preflight asserts the restore matches the
frozen binding — an empty graph must FAIL, not pass by vacuity.
"""

import json
import os

import ontology_frozen as F
import ontology_snapshot as S
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("EMBEDDINGTON_BATTERY") != "1",
    reason="live battery: set EMBEDDINGTON_BATTERY=1 with the stack restored",
)


@pytest.fixture(scope="module")
def db():
    return S._db()


def test_stack_matches_the_frozen_binding(db):
    counts = list(
        db.aql.execute("RETURN {entities: LENGTH(entities_v2), edges: LENGTH(relationships_v2)}")
    )[0]
    assert counts["entities"] == F.EXPECTED_ENTITIES
    assert counts["edges"] == F.EXPECTED_EDGES


def test_committed_snapshot_matches_a_fresh_run(db):
    """The committed baseline must be reproducible from the same stack."""
    committed = json.loads(S.SNAPSHOT_PATH.read_text())
    fresh = S.collect(db)
    assert fresh["fragmentation"] == committed["fragmentation"]
    assert fresh["topology"] == committed["topology"]
    assert fresh["paths"] == committed["paths"]
    assert fresh["release_purity"]["release_purity"] == pytest.approx(
        committed["release_purity"]["release_purity"]
    )


def test_snapshot_records_the_frozen_constants():
    committed = json.loads(S.SNAPSHOT_PATH.read_text())
    assert committed["frozen"]["hub_degree_threshold"] == F.HUB_DEGREE_THRESHOLD
    assert committed["frozen"]["frozen_on"] == F.FROZEN_ON
    assert committed["frozen"]["pair_set_size"] == F.PAIR_SET_SIZE


def test_non_independent_metrics_are_labelled():
    """Spec §4/M1 forbids citing these as evidence a fix worked."""
    committed = json.loads(S.SNAPSHOT_PATH.read_text())
    assert "noise.noise_rate" in committed["non_independent_metrics"]
    assert "fragmentation.fragmentation_rate" in committed["non_independent_metrics"]
