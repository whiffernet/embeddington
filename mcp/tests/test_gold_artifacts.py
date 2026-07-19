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
