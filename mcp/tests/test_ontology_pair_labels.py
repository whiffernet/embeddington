"""Tests for the extrinsic-floor pipeline orchestrator (spec §4/M2, revised).

fetch_paths is the only stage touching live Arango; it is tested here with a
MagicMock shortest_path, mirroring test_tools.py's existing fake_arango
convention rather than reimplementing AQL faking.
"""

import json
from unittest.mock import MagicMock

import ontology_pair_labels as O


def _pair(n, from_id="entities_v2/a", to_id="entities_v2/b"):
    return {
        "n": n,
        "from_id": from_id,
        "to_id": to_id,
        "from_name": f"From{n}",
        "to_name": f"To{n}",
        "from_type": "Feature",
        "to_type": "Product",
        "duplicate_of": None,
    }


def test_fetch_paths_renders_a_real_path():
    fake_arango = MagicMock()
    fake_arango.shortest_path.return_value = {
        "nodes": [
            {"id": "entities_v2/a", "name": "From1", "type": "Feature"},
            {"id": "entities_v2/b", "name": "To1", "type": "Product"},
        ],
        "edges": [{"source": "entities_v2/a", "target": "entities_v2/b", "predicate": "CONTAINS"}],
    }
    result = O.fetch_paths([_pair(1)], fake_arango)
    assert result == [
        {
            "n": 1,
            "no_path": False,
            "abstained": False,
            "reason": "",
            "path_text": "From1 (Feature)  --[CONTAINS]-->  To1 (Product)",
            "hop_count": 1,
        }
    ]
    fake_arango.shortest_path.assert_called_once_with("entities_v2/a", "entities_v2/b", max_hops=4)


def test_fetch_paths_records_no_path():
    fake_arango = MagicMock()
    fake_arango.shortest_path.return_value = None
    result = O.fetch_paths([_pair(1)], fake_arango)
    assert result == [
        {
            "n": 1,
            "no_path": True,
            "abstained": False,
            "reason": "",
            "path_text": "",
            "hop_count": None,
        }
    ]


def test_fetch_paths_preserves_pair_order_for_multiple_pairs():
    fake_arango = MagicMock()
    fake_arango.shortest_path.side_effect = [None, None]
    result = O.fetch_paths([_pair(1), _pair(2)], fake_arango)
    assert [r["n"] for r in result] == [1, 2]


def test_build_score_marks_pre_fix_no_path_pairs_separately():
    paths = [{"n": 1, "no_path": True, "path_text": "", "hop_count": None}]
    result = O.build_score(paths, embedding_scores={}, judge_labels={})
    assert result["pairs"][0] == {
        "n": 1,
        "status": "pre_fix_no_path",
        "label": None,
        "no_path": True,
    }


def test_build_score_routes_a_pathed_pair_through_consensus():
    paths = [{"n": 1, "no_path": False, "path_text": "A --[X]--> B", "hop_count": 1}]
    embedding_scores = {1: {"sim": 0.9, "pct": 0.95, "vote": "good"}}
    judge_labels = {1: "meaningful"}
    result = O.build_score(paths, embedding_scores, judge_labels)
    assert result["pairs"][0] == {
        "n": 1,
        "status": "auto",
        "label": "meaningful",
        "no_path": False,
    }
    assert result["escalated"] == []


def test_build_score_collects_escalated_pairs():
    paths = [{"n": 1, "no_path": False, "path_text": "A --[X]--> B", "hop_count": 1}]
    embedding_scores = {1: {"sim": 0.5, "pct": 0.5, "vote": "abstain"}}
    judge_labels = {1: "trivial"}
    result = O.build_score(paths, embedding_scores, judge_labels)
    assert result["pairs"][0]["status"] == "escalate"
    assert result["escalated"] == [1]


def test_select_stage_writes_the_frozen_extrinsic_pairs_artifact(tmp_path, monkeypatch):
    import ontology_pairs as P

    fake_pool = [
        {
            "from_id": f"entities_v2/f{i}",
            "to_id": f"entities_v2/t{i}",
            "from_type": "Feature",
            "to_type": "Product",
            "from_name": f"From{i}",
            "to_name": f"To{i}",
        }
        for i in range(250)
    ]
    monkeypatch.setattr(P, "load_pairs", lambda: {"pairs": fake_pool})
    monkeypatch.setattr(O, "ONTOLOGY_DIR", tmp_path)

    subset = O.select_stage(write=True)
    assert subset["size"] == 200
    assert subset["unique_size"] == 190

    written = json.loads((tmp_path / "extrinsic-pairs.json").read_text())
    assert written["fingerprint"] == subset["fingerprint"]
    assert len(written["pairs"]) == 200


def test_noise_floor_flip_rate_all_duplicates_agree():
    extrinsic_pairs = [
        {"n": 1, "duplicate_of": None},
        {"n": 2, "duplicate_of": 1},
    ]
    final_labels = {1: "meaningful", 2: "meaningful"}
    assert O.noise_floor_flip_rate(extrinsic_pairs, final_labels) == 0.0


def test_noise_floor_flip_rate_counts_disagreements():
    extrinsic_pairs = [
        {"n": 1, "duplicate_of": None},
        {"n": 2, "duplicate_of": 1},
        {"n": 3, "duplicate_of": None},
        {"n": 4, "duplicate_of": 3},
    ]
    final_labels = {1: "meaningful", 2: "trivial", 3: "none", 4: "none"}
    import pytest

    assert O.noise_floor_flip_rate(extrinsic_pairs, final_labels) == pytest.approx(0.5)


def test_noise_floor_flip_rate_skips_pairs_missing_a_label():
    # e.g. one side of the duplicate pair is pre_fix_no_path (label=None)
    extrinsic_pairs = [{"n": 1, "duplicate_of": None}, {"n": 2, "duplicate_of": 1}]
    final_labels = {1: "meaningful", 2: None}
    assert O.noise_floor_flip_rate(extrinsic_pairs, final_labels) == 0.0


def test_finalize_stage_merges_auto_and_escalated_and_computes_rate():
    import pytest

    scored_pairs = [
        {"n": 1, "status": "auto", "label": "meaningful", "no_path": False},
        {"n": 2, "status": "escalate", "label": None, "no_path": False},
        {"n": 3, "status": "pre_fix_no_path", "label": None, "no_path": True},
    ]
    extrinsic_pairs = [
        {"n": 1, "duplicate_of": None, "from_id": "a", "to_id": "b"},
        {"n": 2, "duplicate_of": None, "from_id": "c", "to_id": "d"},
        {"n": 3, "duplicate_of": None, "from_id": "e", "to_id": "f"},
    ]
    escalated_labels = {2: {"label": "trivial", "low_confidence": False}}

    snapshot = O.finalize_stage(scored_pairs, extrinsic_pairs, escalated_labels)

    by_n = {p["n"]: p for p in snapshot["pairs"]}
    assert by_n[1]["label"] == "meaningful"
    assert by_n[1]["source"] == "consensus"
    assert by_n[2]["label"] == "trivial"
    assert by_n[2]["source"] == "human"
    assert by_n[3]["label"] is None
    assert by_n[3]["status"] == "pre_fix_no_path"

    assert snapshot["total_scored"] == 2  # excludes pre_fix_no_path from the rate denominator
    assert snapshot["meaningful_path_rate"] == pytest.approx(1 / 2)
    assert snapshot["pre_fix_no_path_count"] == 1
    assert snapshot["noise_floor_comparable_count"] == 0


def test_finalize_stage_raises_on_unresolved_escalation():
    import pytest

    scored_pairs = [{"n": 1, "status": "escalate", "label": None, "no_path": False}]
    extrinsic_pairs = [{"n": 1, "duplicate_of": None, "from_id": "a", "to_id": "b"}]
    with pytest.raises(ValueError, match="unresolved escalation"):
        O.finalize_stage(scored_pairs, extrinsic_pairs, escalated_labels={})


def test_finalize_stage_marks_low_confidence_escalations_as_judge_fallback():
    scored_pairs = [
        {"n": 1, "status": "escalate", "label": None, "no_path": False},
        {"n": 2, "status": "escalate", "label": None, "no_path": False},
    ]
    extrinsic_pairs = [
        {"n": 1, "duplicate_of": None, "from_id": "a", "to_id": "b"},
        {"n": 2, "duplicate_of": None, "from_id": "c", "to_id": "d"},
    ]
    escalated_labels = {
        1: {"label": "trivial", "low_confidence": True},
        2: {"label": "meaningful", "low_confidence": False},
    }

    snapshot = O.finalize_stage(scored_pairs, extrinsic_pairs, escalated_labels)

    by_n = {p["n"]: p for p in snapshot["pairs"]}
    assert by_n[1]["source"] == "judge_fallback"
    assert by_n[2]["source"] == "human"


def test_finalize_stage_noise_floor_comparable_count_matches_flip_rate_denominator():
    scored_pairs = [
        {"n": 1, "status": "auto", "label": "meaningful", "no_path": False},
        {"n": 2, "status": "auto", "label": "trivial", "no_path": False},
        {"n": 3, "status": "auto", "label": "meaningful", "no_path": False},
        {"n": 4, "status": "pre_fix_no_path", "label": None, "no_path": True},
    ]
    extrinsic_pairs = [
        {"n": 1, "duplicate_of": None, "from_id": "a", "to_id": "b"},
        {"n": 2, "duplicate_of": 1, "from_id": "c", "to_id": "d"},
        {"n": 3, "duplicate_of": None, "from_id": "e", "to_id": "f"},
        {"n": 4, "duplicate_of": 3, "from_id": "g", "to_id": "h"},
    ]

    snapshot = O.finalize_stage(scored_pairs, extrinsic_pairs, escalated_labels={})

    # Pair 2 is comparable to pair 1 (both labeled); pair 4 is pre_fix_no_path
    # so it has no label and is excluded -- only one comparable duplicate.
    assert snapshot["noise_floor_comparable_count"] == 1


def test_fetch_paths_records_abstention_as_its_own_outcome():
    from ontology_pair_labels import fetch_paths

    class _Arango:
        def shortest_path(self, from_id, to_id, max_hops):
            if from_id.endswith("hubby"):
                return {
                    "nodes": [],
                    "edges": [],
                    "abstained": True,
                    "reason": "3 candidate path(s) within 4 hops, none usable: ...",
                    "hubs": [
                        {
                            "id": "entities_v2/role__admin",
                            "name": "admin",
                            "type": "Role",
                            "degree": 26602,
                        }
                    ],
                }
            return None

    rows = fetch_paths(
        [
            {"n": 1, "from_id": "entities_v2/hubby", "to_id": "entities_v2/b"},
            {"n": 2, "from_id": "entities_v2/a", "to_id": "entities_v2/b"},
        ],
        _Arango(),
    )
    assert rows[0] == {
        "n": 1,
        "no_path": False,
        "abstained": True,
        "reason": "3 candidate path(s) within 4 hops, none usable: ...",
        "path_text": "",
        "hop_count": None,
    }
    assert rows[1] == {
        "n": 2,
        "no_path": True,
        "abstained": False,
        "reason": "",
        "path_text": "",
        "hop_count": None,
    }
