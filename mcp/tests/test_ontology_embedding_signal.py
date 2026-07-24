"""Tests for the embedding-signal math: cosine similarity, percentile rank,
and the frozen good/bad/abstain vote (spec §4/M2, revised).
"""

import ontology_embedding_signal as S
import pytest


def test_cosine_identical_vectors_is_one():
    v = [1.0, 2.0, 3.0]
    assert S.cosine(v, v) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors_is_zero():
    assert S.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_opposite_vectors_is_negative_one():
    assert S.cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_percentile_rank_of_minimum_is_zero():
    assert S.percentile_rank(0.0, [1.0, 2.0, 3.0]) == pytest.approx(0.0)


def test_percentile_rank_of_maximum_is_full_population_below():
    assert S.percentile_rank(10.0, [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_percentile_rank_of_median_value():
    # 2 of 4 values (1.0, 1.5) are strictly less than 2.0
    assert S.percentile_rank(2.0, [1.0, 1.5, 2.5, 3.0]) == pytest.approx(0.5)


def test_percentile_rank_empty_population_is_zero():
    assert S.percentile_rank(1.0, []) == 0.0


def test_vote_good_at_and_above_the_frozen_ceiling():
    assert S.embedding_vote(0.90) == "good"
    assert S.embedding_vote(0.95) == "good"
    assert S.embedding_vote(1.0) == "good"


def test_vote_bad_below_the_frozen_floor():
    assert S.embedding_vote(0.59) == "bad"
    assert S.embedding_vote(0.0) == "bad"


def test_vote_abstain_in_the_ambiguous_middle():
    assert S.embedding_vote(0.60) == "abstain"
    assert S.embedding_vote(0.75) == "abstain"
    assert S.embedding_vote(0.899999) == "abstain"


class _FixedVectorEmbed:
    """Fake EmbeddingClient returning a deterministic vector per text.

    Maps each distinct input text to a fixed pseudo-random-but-deterministic
    1024-dim vector (seeded by the text's own hash), so cosine similarity
    between two DIFFERENT texts is stable across runs without needing a real
    embedding model.
    """

    def __init__(self):
        self._cache: dict[str, list[float]] = {}

    def _vector_for(self, text: str) -> list[float]:
        if text not in self._cache:
            seed = sum(ord(c) for c in text)
            self._cache[text] = [((seed * (i + 1)) % 97) / 97.0 for i in range(1024)]
        return self._cache[text]

    async def embed_batch(self, texts):
        return [self._vector_for(t) for t in texts]


@pytest.mark.asyncio
async def test_score_pairs_returns_one_entry_per_pair():
    pairs = [
        {"n": 1, "from_name": "Incident Management", "to_name": "incident table"},
        {"n": 2, "from_name": "admin role", "to_name": "unrelated widget"},
    ]
    pool_names = ["Incident Management", "incident table", "admin role", "unrelated widget", "X"]
    result = await S.score_pairs(pairs, pool_names, _FixedVectorEmbed())
    assert set(result) == {1, 2}
    for entry in result.values():
        assert -1.0 <= entry["sim"] <= 1.0
        assert 0.0 <= entry["pct"] <= 1.0
        assert entry["vote"] in ("good", "bad", "abstain")


@pytest.mark.asyncio
async def test_score_pairs_vote_matches_the_frozen_thresholds():
    pairs = [{"n": 1, "from_name": "Incident Management", "to_name": "incident table"}]
    pool_names = ["Incident Management", "incident table"]
    result = await S.score_pairs(pairs, pool_names, _FixedVectorEmbed())
    assert result[1]["vote"] == S.embedding_vote(result[1]["pct"])


@pytest.mark.asyncio
async def test_score_pairs_embeds_each_distinct_text_once():
    calls = []

    class _CountingEmbed(_FixedVectorEmbed):
        async def embed_batch(self, texts):
            calls.append(list(texts))
            return await super().embed_batch(texts)

    pairs = [{"n": 1, "from_name": "A", "to_name": "B"}]
    pool_names = ["A", "B", "C"]
    await S.score_pairs(pairs, pool_names, _CountingEmbed())
    # One batch call covering every distinct text (2 pair endpoints + 3 pool names,
    # "A" and "B" overlap between the pair and the pool -> 3 distinct texts).
    assert len(calls) == 1
    assert sorted(set(calls[0])) == ["A", "B", "C"]
