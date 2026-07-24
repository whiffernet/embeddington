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
