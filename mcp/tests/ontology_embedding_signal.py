"""Embedding-distance quality signal (spec §4/M2, revised 2026-07-22).

Two independent readings of the same ServiceNow corpus: the graph asserts a
path between two entities; the `technology` Qdrant collection's bge-m3
embeddings say how semantically close their names actually are. A path whose
endpoints sit at population-baseline distance is likely an artifact of graph
density, not a real relationship — see spec §2.7 for the validation (AUC 0.79
detecting `meaningful`).

No numpy dependency — this repo's mcp package deliberately stays on the
stdlib plus its existing four runtime deps (fastmcp, python-arango,
python-dotenv, httpx); 1024-dim cosine similarity needs no vector library.
"""

import math
from typing import Sequence

import ontology_frozen as F


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two equal-length vectors.

    Args:
        a: First vector.
        b: Second vector, same length as `a`.

    Returns:
        Cosine similarity in [-1, 1]. 0.0 if either vector is all-zero.
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def percentile_rank(value: float, population: Sequence[float]) -> float:
    """Fraction of `population` strictly less than `value`.

    Args:
        value: The similarity score to rank.
        population: The baseline distribution to rank it against.

    Returns:
        A fraction in [0, 1]. 0.0 if `population` is empty.
    """
    if not population:
        return 0.0
    return sum(1 for p in population if p < value) / len(population)


def embedding_vote(pct: float) -> str:
    """Classify a percentile rank into the frozen good/bad/abstain vote.

    Bands are pinned in ontology_frozen and validated against Erik's 30-pair
    seed labels (spec §2.7) — do not tune them here; re-validating a changed
    band is a separate, deliberate exercise (see CONCORDANCE_BAR's docstring).

    Args:
        pct: A percentile rank in [0, 1], as returned by percentile_rank.

    Returns:
        "good" if pct >= EMBED_CONFIDENT_GOOD_PCT, "bad" if pct <
        EMBED_CONFIDENT_BAD_PCT, else "abstain".
    """
    if pct >= F.EMBED_CONFIDENT_GOOD_PCT:
        return "good"
    if pct < F.EMBED_CONFIDENT_BAD_PCT:
        return "bad"
    return "abstain"
