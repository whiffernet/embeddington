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
            "path_text": "From1 (Feature)  --[CONTAINS]-->  To1 (Product)",
            "hop_count": 1,
        }
    ]
    fake_arango.shortest_path.assert_called_once_with("entities_v2/a", "entities_v2/b", max_hops=4)


def test_fetch_paths_records_no_path():
    fake_arango = MagicMock()
    fake_arango.shortest_path.return_value = None
    result = O.fetch_paths([_pair(1)], fake_arango)
    assert result == [{"n": 1, "no_path": True, "path_text": "", "hop_count": None}]


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
