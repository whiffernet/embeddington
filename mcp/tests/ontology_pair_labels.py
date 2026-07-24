"""Extrinsic-floor pipeline orchestrator (spec §4/M2, revised 2026-07-22).

Four stages, run in order against the restored battery stack:
  select      -- draw the frozen extrinsic subset from pairs.json (Task 2)
  fetch-paths -- run shortest_path() for every pair, render judge-readable text
  score       -- embedding signal + consensus routing; writes the escalation queue
  finalize    -- merge auto + escalated labels; compute meaningful_path_rate

Usage (from mcp/, against the restored battery stack):
    python3 tests/ontology_pair_labels.py select --write
    python3 tests/ontology_pair_labels.py fetch-paths --write
    # -- external: dispatch the Sonnet judge over ontology/judge_input.json --
    python3 tests/ontology_pair_labels.py score --write
    # -- external: bounded human A/B over ontology/escalation_input.json --
    python3 tests/ontology_pair_labels.py finalize --write
"""

from pathlib import Path
from typing import Any

import ontology_path_render as R

ONTOLOGY_DIR = Path(__file__).resolve().parent / "ontology"


def fetch_paths(pairs: list[dict], arango: Any) -> list[dict]:
    """Run shortest_path() for every pair and render the judge-readable text.

    Args:
        pairs: Extrinsic pair dicts (ontology_extrinsic_pairs.select_extrinsic_subset output).
        arango: An ArangoKGClient (or compatible fake) with shortest_path(from_id, to_id, max_hops).

    Returns:
        One dict per pair, in input order: ``{"n", "no_path", "path_text", "hop_count"}``.
        ``hop_count`` is the number of edges in the path, or None when there is no path.
    """
    results = []
    for pair in pairs:
        path = arango.shortest_path(pair["from_id"], pair["to_id"], max_hops=4)
        if path is None:
            results.append({"n": pair["n"], "no_path": True, "path_text": "", "hop_count": None})
        else:
            results.append(
                {
                    "n": pair["n"],
                    "no_path": False,
                    "path_text": R.render_path(path),
                    "hop_count": len(path["edges"]),
                }
            )
    return results
