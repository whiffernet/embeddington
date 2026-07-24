"""Consensus routing between the embedding vote and the judge label (spec §4/M2, revised).

auto-labeling requires BOTH: the embedding vote is confident (not "abstain")
AND its polarity agrees with the judge's good/bad polarity (meaningful is
"good", trivial/none are "bad"). Everything else escalates to a bounded human
A/B session — never a rubric grade from scratch. Validated 2026-07-22 against
Erik's 30-pair seed set at 91% (10/11) consensus-zone concordance; see
mcp/tests/ontology/seed-validation.json and this module's regression test.
"""


def _judge_polarity(judge_label: str) -> str:
    """Map judge label to polarity.

    Args:
        judge_label: The judge's label — "meaningful", "trivial", or "none".

    Returns:
        "good" if judge_label is "meaningful", else "bad".
    """
    return "good" if judge_label == "meaningful" else "bad"


def route(vote: str, judge_label: str) -> dict:
    """Route a pair to auto-label or escalate.

    Args:
        vote: The embedding signal's vote — "good", "bad", or "abstain"
            (ontology_embedding_signal.embedding_vote).
        judge_label: The Sonnet judge's label — "meaningful", "trivial", or "none".

    Returns:
        ``{"status": "auto", "label": judge_label}`` when the vote is
        confident and agrees with the judge's polarity, else
        ``{"status": "escalate", "label": None}``.
    """
    if vote != "abstain" and vote == _judge_polarity(judge_label):
        return {"status": "auto", "label": judge_label}
    return {"status": "escalate", "label": None}


def concordance(final_labels: dict[int, str], human_labels: dict[int, str]) -> float:
    """Fraction of shared pair ids where the two label sets agree exactly.

    Args:
        final_labels: Pair id -> label, e.g. the pipeline's auto-labeled output.
        human_labels: Pair id -> label, e.g. a human's ground-truth labels.

    Returns:
        Agreement fraction over ids present in BOTH dicts. 0.0 if there is no
        overlap.
    """
    shared = set(final_labels) & set(human_labels)
    if not shared:
        return 0.0
    return sum(1 for n in shared if final_labels[n] == human_labels[n]) / len(shared)
