"""Tests for consensus routing and the concordance regression tripwire (spec §4/M2, revised).

The regression test replays the real 2026-07-22 seed-label validation
(mcp/tests/ontology/seed-validation.json) through route() and asserts it
still reproduces the historical 11-auto/19-escalate split and >= 0.85
concordance on the auto-labeled rows. A change to route()'s logic that breaks
this is exactly the "future re-validation drops below the bar" case spec
§4/M2 requires the pipeline to fail closed on.
"""

import json
from pathlib import Path

import ontology_consensus as C
import ontology_frozen as F
import pytest

SEED_PATH = Path(__file__).resolve().parent / "ontology" / "seed-validation.json"


def test_route_auto_labels_when_vote_and_judge_agree_good():
    result = C.route("good", "meaningful")
    assert result == {"status": "auto", "label": "meaningful"}


def test_route_auto_labels_when_vote_and_judge_agree_bad():
    result = C.route("bad", "trivial")
    assert result == {"status": "auto", "label": "trivial"}
    assert C.route("bad", "none") == {"status": "auto", "label": "none"}


def test_route_escalates_on_abstain_regardless_of_judge():
    assert C.route("abstain", "meaningful") == {"status": "escalate", "label": None}
    assert C.route("abstain", "trivial") == {"status": "escalate", "label": None}


def test_route_escalates_when_vote_and_judge_disagree():
    assert C.route("good", "trivial") == {"status": "escalate", "label": None}
    assert C.route("bad", "meaningful") == {"status": "escalate", "label": None}


def test_concordance_is_fraction_matching_over_shared_keys():
    final = {1: "meaningful", 2: "trivial", 3: "none"}
    human = {1: "meaningful", 2: "meaningful", 3: "none"}
    assert C.concordance(final, human) == pytest.approx(2 / 3)


def test_concordance_ignores_keys_not_in_both():
    final = {1: "meaningful", 2: "trivial"}
    human = {1: "meaningful", 3: "none"}
    assert C.concordance(final, human) == pytest.approx(1.0)


def test_concordance_empty_overlap_is_zero():
    assert C.concordance({1: "meaningful"}, {2: "trivial"}) == 0.0


def _meaningful_or_not(label: str) -> str:
    """Collapse a 3-class label to the binary distinction the pipeline actually gates on.

    meaningful_path_rate (spec Sec 4/M2) only ever checks label == "meaningful";
    trivial vs none is never separately consumed downstream. The 91% (10/11)
    consensus-zone concordance validated in spec Sec 2.7 was measured on this
    binary distinction, NOT on exact 3-class label match -- the judge and Erik
    disagree on "trivial" vs "none" far more often than on "meaningful" vs
    not, so an exact-label concordance check would (and does) score much
    lower on the same data without indicating anything is actually wrong.
    """
    return "meaningful" if label == "meaningful" else "not-meaningful"


def test_regression_seed_validation_reproduces_historical_split_and_clears_bar():
    rows = json.loads(SEED_PATH.read_text())["rows"]

    statuses = {}
    final_labels = {}
    for row in rows:
        routed = C.route(row["vote"], row["judge_label"])
        statuses[row["n"]] = routed["status"]
        if routed["status"] == "auto":
            final_labels[row["n"]] = routed["label"]

    auto_count = sum(1 for s in statuses.values() if s == "auto")
    escalate_count = sum(1 for s in statuses.values() if s == "escalate")
    assert auto_count == 11
    assert escalate_count == 19

    human_labels = {row["n"]: row["user_label"] for row in rows}

    # Binary concordance is the bar spec Sec 4/M2 actually pins (see
    # _meaningful_or_not's docstring) -- exact-label concordance is a
    # different, strictly harder number and is asserted separately below
    # purely as a documented, non-gating observation.
    final_binary = {n: _meaningful_or_not(label) for n, label in final_labels.items()}
    human_binary = {n: _meaningful_or_not(label) for n, label in human_labels.items()}
    binary_score = C.concordance(final_binary, human_binary)
    assert binary_score == pytest.approx(10 / 11)

    assert binary_score >= F.CONCORDANCE_BAR

    # Documented, non-gating: exact 3-class concordance on this same data is
    # much lower (5/11) because of trivial-vs-none disagreement, not because
    # routing or auto-labeling is broken.
    exact_score = C.concordance(final_labels, human_labels)
    assert exact_score == pytest.approx(5 / 11)
