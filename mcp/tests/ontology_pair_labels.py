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

import argparse
import json
from pathlib import Path
from typing import Any

import ontology_consensus as C
import ontology_extrinsic_pairs as E
import ontology_frozen as F
import ontology_pairs as P
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


def build_score(
    paths: list[dict],
    embedding_scores: dict[int, dict],
    judge_labels: dict[int, str],
) -> dict:
    """Route every fetched path through consensus, tracking pre-fix no_path separately.

    A pair that already has no path before any fix ships has nothing to
    judge or route — it is recorded as its own status so the (later PR's)
    per-pair no_path scoring has a pre-fix reference to diff against.

    Args:
        paths: fetch_paths() output.
        embedding_scores: ontology_embedding_signal.score_pairs() output,
            keyed by ``n`` — only present for pairs with a path.
        judge_labels: ontology_judge_io.read_judge_output() output, keyed by
            ``n`` — only present for pairs with a path.

    Returns:
        ``{"pairs": [{"n", "status", "label", "no_path"}, ...], "escalated": [n, ...]}``.
        ``status`` is one of "pre_fix_no_path", "auto", "escalate".
    """
    result_pairs = []
    escalated = []
    for path in paths:
        n = path["n"]
        if path["no_path"]:
            result_pairs.append(
                {"n": n, "status": "pre_fix_no_path", "label": None, "no_path": True}
            )
            continue
        vote = embedding_scores[n]["vote"]
        judge_label = judge_labels[n]
        routed = C.route(vote, judge_label)
        result_pairs.append(
            {"n": n, "status": routed["status"], "label": routed["label"], "no_path": False}
        )
        if routed["status"] == "escalate":
            escalated.append(n)
    return {"pairs": result_pairs, "escalated": escalated}


def select_stage(write: bool) -> dict:
    """Draw the frozen extrinsic subset from the already-frozen pairs.json pool.

    Args:
        write: If True, write the committed artifact to ontology/extrinsic-pairs.json.

    Returns:
        The subset dict from ontology_extrinsic_pairs.select_extrinsic_subset.
    """
    pool = P.load_pairs()["pairs"]
    unique_size = F.EXTRINSIC_SET_SIZE - F.EXTRINSIC_DUPLICATE_COUNT
    subset = E.select_extrinsic_subset(pool, unique_size, F.EXTRINSIC_DUPLICATE_COUNT)
    subset["fingerprint"] = E.fingerprint_extrinsic_set(subset["pairs"])
    if write:
        out = ONTOLOGY_DIR / "extrinsic-pairs.json"
        out.write_text(json.dumps(subset, indent=2, sort_keys=True) + "\n")
        print(f"wrote {out}: {subset['size']} pairs ({subset['unique_size']} unique)")
    return subset


def main() -> None:
    """Orchestrate the extrinsic-floor pipeline stages."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["select", "fetch-paths", "score", "finalize"])
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    if args.stage == "select":
        select_stage(args.write)
    else:
        raise SystemExit(f"stage {args.stage!r} is not implemented yet in this task")


if __name__ == "__main__":
    main()
