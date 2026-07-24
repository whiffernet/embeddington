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


def noise_floor_flip_rate(
    extrinsic_pairs: list[dict], final_labels: dict[int, str | None]
) -> float:
    """Fraction of blind duplicate pairs whose final label disagrees with its original.

    Args:
        extrinsic_pairs: The frozen extrinsic set (carries ``duplicate_of``).
        final_labels: Pair ``n`` -> final label (None for pre_fix_no_path pairs).

    Returns:
        Disagreement fraction over duplicate pairs where BOTH sides have a
        label. 0.0 if there are no comparable duplicate pairs.
    """
    comparable = []
    for pair in extrinsic_pairs:
        if pair["duplicate_of"] is None:
            continue
        original_label = final_labels.get(pair["duplicate_of"])
        dupe_label = final_labels.get(pair["n"])
        if original_label is None or dupe_label is None:
            continue
        comparable.append(original_label != dupe_label)
    if not comparable:
        return 0.0
    return sum(comparable) / len(comparable)


def finalize_stage(
    scored_pairs: list[dict], extrinsic_pairs: list[dict], escalated_labels: dict[int, str]
) -> dict:
    """Merge auto + escalated labels into the committed pre-fix reference snapshot.

    Args:
        scored_pairs: build_score()["pairs"] output.
        extrinsic_pairs: The frozen extrinsic set (for duplicate_of lookups).
        escalated_labels: Resolved labels for every ``n`` that build_score()
            marked "escalate" — from the bounded human A/B session, or (for
            any pair the session did not reach) the judge's own label, which
            the caller must flag as low-confidence in the artifact separately.

    Returns:
        The full snapshot dict: ``pairs`` (per-pair n/status/label/source/no_path),
        ``total_scored``, ``meaningful_path_rate``, ``pre_fix_no_path_count``,
        ``noise_floor_flip_rate``.

    Raises:
        ValueError: If any "escalate"-status pair has no entry in escalated_labels.
    """
    final_pairs = []
    final_labels: dict[int, str | None] = {}
    for scored in scored_pairs:
        n = scored["n"]
        if scored["status"] == "pre_fix_no_path":
            final_pairs.append({"n": n, "status": "pre_fix_no_path", "label": None, "source": None})
            final_labels[n] = None
        elif scored["status"] == "auto":
            final_pairs.append(
                {"n": n, "status": "auto", "label": scored["label"], "source": "consensus"}
            )
            final_labels[n] = scored["label"]
        else:  # escalate
            if n not in escalated_labels:
                raise ValueError(f"unresolved escalation for n={n}")
            final_pairs.append(
                {"n": n, "status": "escalate", "label": escalated_labels[n], "source": "human"}
            )
            final_labels[n] = escalated_labels[n]

    scored_labels = {p["n"]: p["label"] for p in final_pairs if p["label"] is not None}
    meaningful_count = sum(1 for label in scored_labels.values() if label == "meaningful")

    return {
        "pairs": final_pairs,
        "total_scored": len(scored_labels),
        "meaningful_path_rate": meaningful_count / len(scored_labels) if scored_labels else 0.0,
        "pre_fix_no_path_count": sum(1 for p in final_pairs if p["status"] == "pre_fix_no_path"),
        "noise_floor_flip_rate": noise_floor_flip_rate(extrinsic_pairs, final_labels),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["select", "fetch-paths", "score", "finalize"])
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    if args.stage == "select":
        select_stage(args.write)
        return

    if args.stage == "fetch-paths":
        _fetch_paths_stage(args.write)
        return

    if args.stage == "score":
        _score_stage(args.write)
        return

    if args.stage == "finalize":
        _finalize_stage_cli(args.write)
        return


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _fetch_paths_stage(write: bool) -> None:
    """Run fetch_paths() over the committed extrinsic set against the battery Arango."""
    import os

    from arango_client import ArangoKGClient

    extrinsic = _load_json(ONTOLOGY_DIR / "extrinsic-pairs.json")
    arango = ArangoKGClient(
        url=os.environ.get("BATTERY_ARANGO_URL", "http://localhost:19412"),
        database=os.environ.get("BATTERY_ARANGO_DB", "technology_kg"),
        username=os.environ.get("BATTERY_ARANGO_USER", "root"),
        password=os.environ["BATTERY_ARANGO_PASSWORD"],
    )
    paths = fetch_paths(extrinsic["pairs"], arango)
    if write:
        out = ONTOLOGY_DIR / "pair-paths.json"
        out.write_text(json.dumps(paths, indent=2) + "\n")
        no_path_count = sum(1 for p in paths if p["no_path"])
        print(f"wrote {out}: {len(paths)} pairs, {no_path_count} pre-fix no_path")

        judge_entries = []
        pairs_by_n = {p["n"]: p for p in extrinsic["pairs"]}
        for path in paths:
            if path["no_path"]:
                continue
            pair = pairs_by_n[path["n"]]
            judge_entries.append(
                {
                    "n": path["n"],
                    "from_name": pair["from_name"],
                    "to_name": pair["to_name"],
                    "path_text": path["path_text"],
                }
            )
        import ontology_judge_io as J

        judge_out = ONTOLOGY_DIR / "judge_input.json"
        J.write_judge_input(judge_entries, judge_out)
        print(f"wrote {judge_out}: {len(judge_entries)} pairs to judge")


def _score_stage(write: bool) -> None:
    """Score every pair via the embedding signal and route through consensus."""
    import asyncio

    import ontology_embedding_signal as S
    import ontology_judge_io as J
    from embedding_client import EmbeddingClient

    extrinsic = _load_json(ONTOLOGY_DIR / "extrinsic-pairs.json")
    paths = _load_json(ONTOLOGY_DIR / "pair-paths.json")
    pairs_by_n = {p["n"]: p for p in extrinsic["pairs"]}
    pathed_ns = {p["n"] for p in paths if not p["no_path"]}
    judge_labels = J.read_judge_output(ONTOLOGY_DIR / "judge_output.json", expected_ns=pathed_ns)

    import config

    client = EmbeddingClient(url=config.EMBED_URL, index=F.EMBED_INDEX)
    pool_names = sorted(
        {p["from_name"] for p in extrinsic["pairs"]} | {p["to_name"] for p in extrinsic["pairs"]}
    )
    pathed_pairs = [pairs_by_n[n] for n in pathed_ns]
    embedding_scores = asyncio.run(S.score_pairs(pathed_pairs, pool_names, client))

    scored = build_score(paths, embedding_scores, judge_labels)
    if write:
        out = ONTOLOGY_DIR / "scored-pairs.json"
        out.write_text(json.dumps(scored, indent=2) + "\n")

        escalated_entries = []
        for n in scored["escalated"]:
            pair = pairs_by_n[n]
            path = next(p for p in paths if p["n"] == n)
            escalated_entries.append(
                {
                    "n": n,
                    "from_name": pair["from_name"],
                    "to_name": pair["to_name"],
                    "path_text": path["path_text"],
                    "judge_label": judge_labels[n],
                }
            )
        esc_out = ONTOLOGY_DIR / "escalation_input.json"
        esc_out.write_text(json.dumps(escalated_entries, indent=2))
        print(
            f"wrote {out}: {len(scored['pairs'])} scored, "
            f"{len(scored['escalated'])} escalated to {esc_out}"
        )


def _finalize_stage_cli(write: bool) -> None:
    """Merge the resolved escalation queue and write the committed snapshot."""
    extrinsic = _load_json(ONTOLOGY_DIR / "extrinsic-pairs.json")
    scored = _load_json(ONTOLOGY_DIR / "scored-pairs.json")

    escalation_resolution_path = ONTOLOGY_DIR / "escalation_output.json"
    escalated_labels: dict[int, str] = {}
    if escalation_resolution_path.exists():
        for row in _load_json(escalation_resolution_path):
            escalated_labels[row["n"]] = row["label"]

    snapshot = finalize_stage(scored["pairs"], extrinsic["pairs"], escalated_labels)
    snapshot["extrinsic_set_fingerprint"] = extrinsic["fingerprint"]
    snapshot["frozen"] = {
        "extrinsic_set_size": F.EXTRINSIC_SET_SIZE,
        "extrinsic_duplicate_count": F.EXTRINSIC_DUPLICATE_COUNT,
        "concordance_bar": F.CONCORDANCE_BAR,
    }
    if write:
        out = ONTOLOGY_DIR / "pair-labels-snapshot.json"
        out.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        print(
            f"wrote {out}: meaningful_path_rate={snapshot['meaningful_path_rate']:.3f} "
            f"noise_floor_flip_rate={snapshot['noise_floor_flip_rate']:.3f} "
            f"pre_fix_no_path={snapshot['pre_fix_no_path_count']}"
        )


if __name__ == "__main__":
    main()
