"""Validate the frozen gold artifacts' internal consistency (spec §3.3).

These run in CI (no live stack): they check the committed files against each
other, not against a database.
"""

import json
from pathlib import Path

import pytest

GOLD = Path(__file__).resolve().parent / "gold"
ALLOWED = {"relevant", "marginal", "irrelevant"}


@pytest.fixture(scope="module")
def pools():
    return json.loads((GOLD / "pools.json").read_text())


@pytest.fixture(scope="module")
def labels():
    return json.loads((GOLD / "labels.json").read_text())


@pytest.fixture(scope="module")
def pools_identifier():
    return json.loads((GOLD / "pools-identifier.json").read_text())


@pytest.fixture(scope="module")
def labels_identifier():
    return json.loads((GOLD / "labels-identifier.json").read_text())


@pytest.fixture(scope="module")
def token_calibration():
    return json.loads((GOLD / "token_calibration.json").read_text())


def test_gold_binding_is_the_frozen_baseline(pools):
    assert pools["binding"]["baseline"] == "baseline-2026-07b"
    assert pools["binding"]["points"] == 152194
    assert pools["binding"]["edges"] == 683651


def test_labels_cover_pools_exactly(pools, labels):
    assert set(labels) == set(pools["queries"]), "label/query name mismatch"
    for name, q in pools["queries"].items():
        assert set(labels[name]) == set(q["edges"]), f"{name}: label ids != pool ids"


def test_labels_are_well_formed(labels):
    for name, per_edge in labels.items():
        for eid, rec in per_edge.items():
            assert rec["label"] in ALLOWED, f"{name}/{eid}: bad label {rec['label']}"
            assert rec["rationale"].strip(), f"{name}/{eid}: empty rationale"


def test_every_query_has_some_relevant(labels):
    starved = [
        n
        for n, per_edge in labels.items()
        if not any(r["label"] == "relevant" for r in per_edge.values())
    ]
    # A query with zero relevant edges can't be scored by gold-recall; if this
    # legitimately happens, it must be an explicit, documented exclusion.
    assert not starved, f"queries with no relevant edges: {starved}"


def test_pr3_floor_is_pinned():
    text = (GOLD / "README.md").read_text()
    assert "PR 3 (#36) acceptance floor" in text
    assert "[M" not in text, "floor still has unfilled placeholders"


class TestTokenCalibration:
    """estimate_tokens vs cl100k_base proxy calibration (#44)."""

    def test_has_rows(self, token_calibration):
        assert len(token_calibration["rows"]) > 0

    def test_rows_well_formed(self, token_calibration):
        for row in token_calibration["rows"]:
            assert row["est"] > 0
            assert row["real"] > 0

    def test_e_in_sane_range(self, token_calibration):
        e = token_calibration["e"]
        assert 0 <= e < 0.5, f"e={e} outside sanity range [0, 0.5)"

    def test_calibrated_bar_matches_formula(self, token_calibration):
        e = token_calibration["e"]
        assert token_calibration["calibrated_bar"] == int(9000 * (1 - e))


# Identifier cohort (2026-07-19) — four NL-phrased queries with controller-verified corpus presence.
# Two pools (id_disc_plugin, id_mim_plugin) are legitimately empty; the KG extraction lane
# cannot resolve dotted plugin identifiers (measured deficiency). Vector lane gates both.


class TestIdentifierCohortGoldArtifacts:
    """Cohort-adapted gold-artifact tests for identifier queries."""

    def test_identifier_binding_is_the_frozen_baseline(self, pools_identifier):
        """Verify the identifier cohort binds to the frozen baseline."""
        assert pools_identifier["binding"]["baseline"] == "baseline-2026-07b"
        assert pools_identifier["binding"]["points"] == 152194
        assert pools_identifier["binding"]["entities"] == 310364
        assert pools_identifier["binding"]["edges"] == 683651

    def test_identifier_labels_cover_pools_exactly(self, pools_identifier, labels_identifier):
        """Verify labels dict covers each pool's edges exactly, including empty pools."""
        assert set(labels_identifier) == set(pools_identifier["queries"]), (
            "label/query name mismatch"
        )
        for name, q in pools_identifier["queries"].items():
            assert set(labels_identifier[name]) == set(q["edges"]), (
                f"{name}: label edge ids != pool edge ids"
            )

    def test_identifier_labels_are_well_formed(self, labels_identifier):
        """Verify non-empty pool labels use allowed vocab and have rationales."""
        for name, per_edge in labels_identifier.items():
            for eid, rec in per_edge.items():
                assert rec["label"] in ALLOWED, f"{name}/{eid}: bad label {rec['label']}"
                assert rec["rationale"].strip(), f"{name}/{eid}: empty rationale"

    def test_identifier_empty_pools_pinned_to_plugin_deficiency(self, pools_identifier):
        """
        Pin the measured deficiency: _extract_entity_hints cannot resolve dotted
        identifiers. This test will fail if the hint extractor is fixed and re-run
        against the corpus, forcing a deliberate update to this cohort.
        """
        empty_pools = {n for n, q in pools_identifier["queries"].items() if not q["edges"]}
        assert empty_pools == {"id_disc_plugin", "id_mim_plugin"}, (
            f"empty pool names diverged from pinned deficiency: {empty_pools}"
        )

    def test_identifier_nonempty_queries_have_some_relevant(
        self, pools_identifier, labels_identifier
    ):
        """Verify each non-empty pool query has ≥1 relevant edge."""
        nonempty_queries = {n for n, q in pools_identifier["queries"].items() if q["edges"]}

        starved = [
            n
            for n in nonempty_queries
            if not any(r["label"] == "relevant" for r in labels_identifier[n].values())
        ]
        assert not starved, f"non-empty identifier queries with no relevant edges: {starved}"
