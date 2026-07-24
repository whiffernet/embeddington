"""Tests for the judge input/output file contract (spec §4/M2, revised).

write_judge_input must never leak a label or duplicate_of — the judge is
blind by construction, not by convention. read_judge_output must reject a
malformed or incomplete file rather than silently accepting a partial run.
"""

import json

import ontology_judge_io as J
import pytest


def test_write_judge_input_emits_only_n_from_to_path(tmp_path):
    entries = [
        {
            "n": 1,
            "from_name": "A",
            "to_name": "B",
            "path_text": "A --[X]--> B",
            "duplicate_of": None,
        },
    ]
    out = tmp_path / "judge_input.json"
    J.write_judge_input(entries, out)

    written = json.loads(out.read_text())
    assert written == [{"n": 1, "from": "A", "to": "B", "path": "A --[X]--> B"}]


def test_read_judge_output_returns_label_by_n(tmp_path):
    data = [
        {"n": 1, "label": "meaningful", "reason": "clear chain"},
        {"n": 2, "label": "trivial", "reason": "hub-mediated"},
    ]
    p = tmp_path / "judge_output.json"
    p.write_text(json.dumps(data))

    result = J.read_judge_output(p, expected_ns={1, 2})
    assert result == {1: "meaningful", 2: "trivial"}


def test_read_judge_output_rejects_missing_pair(tmp_path):
    p = tmp_path / "judge_output.json"
    p.write_text(json.dumps([{"n": 1, "label": "meaningful", "reason": "x"}]))

    with pytest.raises(ValueError, match="missing labels for n="):
        J.read_judge_output(p, expected_ns={1, 2})


def test_read_judge_output_rejects_unknown_pair(tmp_path):
    p = tmp_path / "judge_output.json"
    p.write_text(json.dumps([{"n": 99, "label": "meaningful", "reason": "x"}]))

    with pytest.raises(ValueError, match="unexpected n="):
        J.read_judge_output(p, expected_ns={1})


def test_read_judge_output_rejects_invalid_label(tmp_path):
    p = tmp_path / "judge_output.json"
    p.write_text(json.dumps([{"n": 1, "label": "sort-of", "reason": "x"}]))

    with pytest.raises(ValueError, match="invalid label"):
        J.read_judge_output(p, expected_ns={1})


def test_read_judge_output_rejects_duplicate_n(tmp_path):
    p = tmp_path / "judge_output.json"
    p.write_text(
        json.dumps(
            [
                {"n": 1, "label": "meaningful", "reason": "x"},
                {"n": 1, "label": "trivial", "reason": "y"},
            ]
        )
    )
    with pytest.raises(ValueError, match="duplicate n="):
        J.read_judge_output(p, expected_ns={1})
