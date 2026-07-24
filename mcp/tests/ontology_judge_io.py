"""Judge input/output file contract (spec §4/M2, revised).

The seam between repo code and the externally-dispatched Sonnet judge: this
repo has no Anthropic SDK dependency and adds none. write_judge_input
produces a blind file (no labels, no duplicate_of) for a Claude Code session
to hand to an Agent-tool judge run; read_judge_output validates whatever
comes back before anything downstream trusts it.
"""

import json
from pathlib import Path

VALID_LABELS = {"meaningful", "trivial", "none"}


def write_judge_input(entries: list[dict], path: Path) -> None:
    """Write the blind judge input file.

    Args:
        entries: Dicts each carrying ``n``, ``from_name``, ``to_name``,
            ``path_text`` (label and duplicate_of, if present, are dropped).
        path: Destination file path; parent directory must already exist.
    """
    blind = [
        {"n": e["n"], "from": e["from_name"], "to": e["to_name"], "path": e["path_text"]}
        for e in entries
    ]
    path.write_text(json.dumps(blind, indent=1))


def read_judge_output(path: Path, expected_ns: set[int]) -> dict[int, str]:
    """Validate and load the judge's output file.

    Args:
        path: Path to the judge output JSON array of ``{n, label, reason}``.
        expected_ns: The complete set of pair ids that must be present.

    Returns:
        Dict mapping pair ``n`` to its label.

    Raises:
        ValueError: On a duplicate, unexpected, or missing ``n``, or a label
            outside {"meaningful", "trivial", "none"}.
    """
    rows = json.loads(path.read_text())
    result: dict[int, str] = {}
    for row in rows:
        n = row["n"]
        if n in result:
            raise ValueError(f"duplicate n={n} in {path}")
        if n not in expected_ns:
            raise ValueError(f"unexpected n={n} in {path} (not in the frozen set)")
        if row["label"] not in VALID_LABELS:
            raise ValueError(f"invalid label {row['label']!r} for n={n} in {path}")
        result[n] = row["label"]

    missing = expected_ns - set(result)
    if missing:
        raise ValueError(f"missing labels for n={sorted(missing)} in {path}")
    return result
