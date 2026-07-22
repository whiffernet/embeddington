"""Arithmetic tests for the intrinsic ontology metrics (spec §4/M1).

No live stack: a fake db handle returns canned AQL results, so what is under
test is the metric arithmetic and returned shape. Live sanity ranges are gated
in test_ontology_live.py.
"""

import ontology_metrics as M


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


def test_normalize_aql_collapses_runs_like_budget_normalize_name():
    # budget.normalize_name uses [^a-z0-9]+ -> " " (runs collapse), then strip.
    assert "[^a-z0-9]+" in M.NORMALIZE_AQL
    assert "TRIM" in M.NORMALIZE_AQL


def test_fragmentation_computes_rate_and_splits_generic_from_specific():
    db = FakeDB(
        [
            [
                {
                    "cross_type_concept_count": 100,
                    "entities_in_cross_type_groups": 250,
                    "specific_groups": 70,
                    "specific_entities": 180,
                    "generic_groups": 30,
                    "generic_entities": 70,
                }
            ],
            [{"total": 1000}],
        ]
    )
    out = M.fragmentation(db)
    assert out["cross_type_concept_count"] == 100
    assert out["total_entities"] == 1000
    assert out["fragmentation_rate"] == 0.25
    assert out["specific_entities"] == 180
    assert out["generic_entities"] == 70


def test_fragmentation_survives_aql_returning_null_aggregates():
    """AQL SUM() over zero rows returns null, not 0.

    Verified live: forcing zero matching groups makes every SUM field null.
    Without coercion this raises TypeError on the division.
    """
    db = FakeDB(
        [
            [
                {
                    "cross_type_concept_count": None,
                    "entities_in_cross_type_groups": None,
                    "specific_groups": None,
                    "specific_entities": None,
                    "generic_groups": None,
                    "generic_entities": None,
                }
            ],
            [{"total": 0}],
        ]
    )
    out = M.fragmentation(db)
    assert out["fragmentation_rate"] == 0.0
    assert out["cross_type_concept_count"] == 0
    assert out["specific_entities"] == 0


def test_fragmentation_survives_no_rows_at_all():
    db = FakeDB([[], [{"total": 0}]])
    assert M.fragmentation(db)["fragmentation_rate"] == 0.0


def test_noise_counts_by_category_and_rate():
    db = FakeDB(
        [
            [
                {"name": "configure"},
                {"name": "sn_azure_ad_spoke.AzureAD"},
                {"name": "ci"},
                {"name": "Customer Service Management"},
            ]
        ]
    )
    out = M.noise(db)
    assert out["total_entities"] == 4
    assert out["noise_entities"] == 3
    assert out["noise_rate"] == 0.75
    assert out["by_category"]["generic"] == 1
    assert out["by_category"]["dotted_alias"] == 1
    assert out["by_category"]["too_short"] == 1


def test_topology_computes_leaf_fraction_and_hub_concentration():
    db = FakeDB(
        [
            [{"total_entities": 1000, "leaf_entities": 500}],
            [{"hub_count": 10}],
            [{"total_edges": 2000, "hub_incident_edges": 400}],
        ]
    )
    out = M.topology(db)
    assert out["leaf_fraction"] == 0.5
    assert out["hub_count"] == 10
    assert out["hub_concentration"] == 0.2


def test_release_purity_accepts_decorated_forms_and_rejects_versions():
    """Exact matching alone scores 2.05% live and misses real releases.

    "WashingtonDC Patch 9a" is an unambiguous platform release that exact
    matching drops because the frozen list spells it "washington dc".
    """
    db = FakeDB(
        [
            [
                {"name": "Zurich"},
                {"name": "Zurich Patch 2"},
                {"name": "WashingtonDC Patch 9a"},
                {"name": "Version 20.0"},
                {"name": "Early"},
            ]
        ]
    )
    out = M.release_purity(db)
    assert out["release_entities"] == 5
    assert out["known_release_entities"] == 3
    assert out["release_purity"] == 0.6
    assert sorted(out["unknown_samples"]) == ["Early", "Version 20.0"]
