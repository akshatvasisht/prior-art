"""Tests for the benchmark metrics module."""

from bench.metrics import aggregate, ndcg_at_k, recall_at_k, reciprocal_rank


def test_recall_at_k():
    assert recall_at_k(["a", "b"], ["a", "x", "b"], k=3) == 1.0
    assert recall_at_k(["a", "b"], ["a", "x", "y"], k=3) == 0.5
    assert recall_at_k([], ["a"], k=3) == 0.0


def test_reciprocal_rank():
    assert reciprocal_rank(["a"], ["x", "a", "y"]) == 0.5
    assert reciprocal_rank(["a"], ["a", "x"]) == 1.0
    assert reciprocal_rank(["a"], ["x", "y"]) == 0.0


def test_ndcg_perfect_ranking_is_one():
    score = ndcg_at_k(["a", "b"], ["a", "b", "c"], k=3)
    assert abs(score - 1.0) < 1e-9


def test_ndcg_misranked_is_lower():
    perfect = ndcg_at_k(["a", "b"], ["a", "b", "c"], k=3)
    misranked = ndcg_at_k(["a", "b"], ["c", "a", "b"], k=3)
    assert misranked < perfect


def test_aggregate_means_across_queries():
    per_query = [
        {"recall@5": 1.0, "mrr": 1.0},
        {"recall@5": 0.0, "mrr": 0.0},
    ]
    agg = aggregate(per_query)
    assert agg["recall@5"] == 0.5
    assert agg["mrr"] == 0.5
