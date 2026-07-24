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
from typing import TYPE_CHECKING, Sequence

import ontology_frozen as F

if TYPE_CHECKING:
    from embedding_client import EmbeddingClient


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


# The /embed endpoint's own cap, hit running this pipeline live 2026-07-24
# ("Maximum 100 texts per request") -- not a tunable, a fixed server limit.
_EMBED_REQUEST_LIMIT = 100


async def score_pairs(
    pairs: list[dict], pool_names: list[str], client: "EmbeddingClient"
) -> dict[int, dict]:
    """Score every pair's endpoint-name similarity against a population baseline.

    The population baseline is the full set of distinct entity names already
    present in the frozen pair pool (ontology_pairs.py's pairs.json) — no
    fresh Arango query, so this function needs no database access at all.
    Every distinct text (pair endpoints + population names) is embedded
    exactly once, in as few batch calls as the endpoint's own request-size
    cap allows (the real `/embed` endpoint rejects more than 100 texts per
    request). Population-vs-population cosines are computed once and reused
    for every pair's percentile rank.

    Args:
        pairs: Pair dicts each carrying ``n``, ``from_name``, ``to_name``.
        pool_names: Distinct entity names forming the population baseline.
        client: An EmbeddingClient (or compatible fake) with async embed_batch.

    Returns:
        Dict keyed by pair ``n``, each value ``{"sim": float, "pct": float,
        "vote": str}``.
    """
    pair_texts: list[str] = []
    for pair in pairs:
        pair_texts.append(pair["from_name"])
        pair_texts.append(pair["to_name"])

    distinct_texts = sorted(set(pair_texts) | set(pool_names))
    vec_by_text: dict[str, list[float]] = {}
    for i in range(0, len(distinct_texts), _EMBED_REQUEST_LIMIT):
        chunk = distinct_texts[i : i + _EMBED_REQUEST_LIMIT]
        vectors = await client.embed_batch(chunk)
        vec_by_text.update(zip(chunk, vectors))

    pool_vectors = [vec_by_text[name] for name in pool_names]
    population_cosines = [
        cosine(pool_vectors[i], pool_vectors[j])
        for i in range(len(pool_vectors))
        for j in range(len(pool_vectors))
        if i != j
    ]

    result: dict[int, dict] = {}
    for pair in pairs:
        sim = cosine(vec_by_text[pair["from_name"]], vec_by_text[pair["to_name"]])
        pct = percentile_rank(sim, population_cosines)
        result[pair["n"]] = {"sim": sim, "pct": pct, "vote": embedding_vote(pct)}
    return result
