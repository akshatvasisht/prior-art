"""Tests for the benchmark metrics module."""

from bench.metrics import (
    aggregate,
    condensed_ndcg_at_k,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    success_at_k,
)


def test_recall_at_k():
    # Graded form: any grade>=1 counts as a hit; recall is the fraction of
    # relevant docs found in top-k.
    assert recall_at_k({"a": 1, "b": 1}, ["a", "x", "b"], k=3) == 1.0
    assert recall_at_k({"a": 1, "b": 1}, ["a", "x", "y"], k=3) == 0.5
    assert recall_at_k({}, ["a"], k=3) == 0.0


def test_duplicate_results_do_not_inflate_metrics():
    """Repeated doc IDs in the ranked list are counted once — metrics stay <= 1.0
    and match the de-duplicated ranking."""
    relevant = {"a": 1, "b": 1}
    assert recall_at_k(relevant, ["a", "a", "b"], k=3) == 1.0
    assert recall_at_k(relevant, ["a", "a", "b"], k=3) == recall_at_k(relevant, ["a", "b"], k=3)
    assert ndcg_at_k(relevant, ["a", "a", "b"], k=3) == ndcg_at_k(relevant, ["a", "b"], k=3)
    assert ndcg_at_k(relevant, ["a", "a"], k=3) <= 1.0


def test_reciprocal_rank():
    assert reciprocal_rank({"a": 1}, ["x", "a", "y"]) == 0.5
    assert reciprocal_rank({"a": 1}, ["a", "x"]) == 1.0
    assert reciprocal_rank({"a": 1}, ["x", "y"]) == 0.0


def test_ndcg_perfect_ranking_is_one():
    score = ndcg_at_k({"a": 1, "b": 1}, ["a", "b", "c"], k=3)
    assert abs(score - 1.0) < 1e-9


def test_ndcg_misranked_is_lower():
    perfect = ndcg_at_k({"a": 1, "b": 1}, ["a", "b", "c"], k=3)
    misranked = ndcg_at_k({"a": 1, "b": 1}, ["c", "a", "b"], k=3)
    assert misranked < perfect


def test_metrics_ndcg_graded_relevance():
    """A grade=2 doc retrieved at position 1 must outscore a grade=1 doc at position 1.

    The standard ``(2^rel - 1)`` gain formula gives grade-2 a gain of 3 vs
    grade-1's gain of 1, so the ratio of nDCG(grade=2 alone) / nDCG(grade=1 alone)
    is 3:1 — but both are normalised by their own ideal DCG, so each in
    isolation scores 1.0. To compare we need a shared corpus where positions
    differ: place a grade-2 hit at rank 1 vs rank 2 of the same query.
    """
    # Corpus: one grade-2 doc + filler. Hit at rank 1 = perfect (1.0).
    perfect = ndcg_at_k({"top": 2}, ["top", "x"], k=3)
    # Same query, hit at rank 2 — gain discounted by log2(3) vs log2(2).
    delayed = ndcg_at_k({"top": 2}, ["x", "top"], k=3)
    assert perfect > delayed

    # Grade-2 vs grade-1 on a shared corpus: high-grade hit at rank 1 alongside
    # a low-grade hit at rank 2 must outscore the swap.
    graded = ndcg_at_k({"high": 2, "low": 1}, ["high", "low"], k=3)
    swapped = ndcg_at_k({"high": 2, "low": 1}, ["low", "high"], k=3)
    assert graded > swapped


def test_success_at_k():
    assert success_at_k({"a": 1}, ["x", "a", "y"], k=3) == 1.0
    assert success_at_k({"a": 1}, ["x", "y", "z"], k=3) == 0.0
    # The hit must be within k.
    assert success_at_k({"a": 1}, ["x", "y", "a"], k=2) == 0.0
    assert success_at_k({}, ["a"], k=3) == 0.0


def test_condensed_ndcg_ignores_unjudged_docs():
    """A relevant doc buried under UNJUDGED docs scores 0 on raw nDCG@k but high
    on condensed nDCG — the unjudged docs are dropped before scoring."""
    relevant = {"gold": 1}
    # 'gold' sits at rank 4, behind three unjudged (not in relevant) docs.
    ranked = ["unjudged1", "unjudged2", "unjudged3", "gold"]
    assert ndcg_at_k(relevant, ranked, k=3) == 0.0  # gold outside top-3
    assert condensed_ndcg_at_k(relevant, ranked, k=3) == 1.0  # condensed -> gold first
    # No relevant doc retrieved at all stays 0 under both.
    assert condensed_ndcg_at_k(relevant, ["a", "b"], k=3) == 0.0


def test_aggregate_means_across_queries():
    per_query = [
        {"recall@5": 1.0, "mrr": 1.0},
        {"recall@5": 0.0, "mrr": 0.0},
    ]
    agg = aggregate(per_query)
    assert agg["recall@5"] == 0.5
    assert agg["mrr"] == 0.5
