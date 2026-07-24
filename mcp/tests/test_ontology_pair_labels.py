"""Tests for the extrinsic-floor pipeline orchestrator (spec §4/M2, revised).

fetch_paths is the only stage touching live Arango; it is tested here with a
MagicMock shortest_path, mirroring test_tools.py's existing fake_arango
convention rather than reimplementing AQL faking.
"""

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
