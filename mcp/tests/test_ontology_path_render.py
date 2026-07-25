"""Tests for rendering a shortest_path() result into judge-readable chain text.

The format must match what was used and validated in the 2026-07-22 seed-label
run — "A (Type)  --[PRED]-->  B (Type)" with a two-space gutter on each side of
the arrow — since the consensus concordance bar (spec §4/M2) was measured
against text in exactly this shape.
"""

import ontology_path_render as R


def test_renders_a_two_hop_path():
    path = {
        "nodes": [
            {"id": "entities_v2/a", "name": "Incident Management", "type": "Module"},
            {"id": "entities_v2/b", "name": "incident", "type": "Table"},
            {"id": "entities_v2/c", "name": "task", "type": "Table"},
        ],
        "edges": [
            {"source": "entities_v2/a", "target": "entities_v2/b", "predicate": "USES_TABLE"},
            {"source": "entities_v2/b", "target": "entities_v2/c", "predicate": "EXTENDS_TABLE"},
        ],
    }
    assert R.render_path(path) == (
        "Incident Management (Module)  --[USES_TABLE]-->  incident (Table)  "
        "--[EXTENDS_TABLE]-->  task (Table)"
    )


def test_renders_none_as_empty_string():
    assert R.render_path(None) == ""


def test_renders_no_path_shape_as_empty_string():
    assert R.render_path({"nodes": [], "edges": [], "no_path": True}) == ""


def test_renders_single_hop():
    path = {
        "nodes": [
            {"id": "entities_v2/a", "name": "A", "type": "Feature"},
            {"id": "entities_v2/b", "name": "B", "type": "Product"},
        ],
        "edges": [{"source": "entities_v2/a", "target": "entities_v2/b", "predicate": "CONTAINS"}],
    }
    assert R.render_path(path) == "A (Feature)  --[CONTAINS]-->  B (Product)"
