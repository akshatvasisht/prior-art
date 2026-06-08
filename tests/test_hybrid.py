"""Tests for core.hybrid — RRF fusion and BM25 index."""

import pytest

from priorart.core.hybrid import (
    DEFAULT_RRF_K,
    BM25Index,
    _tokenize,
    reciprocal_rank_fusion,
)


def test_tokenize_lowercases_and_splits_alphanumeric():
    assert _tokenize("HTTP/2 Client!") == ["http", "2", "client"]


def test_tokenize_handles_none_and_empty():
    assert _tokenize("") == []
    # The function casts to str via the regex's `or ""`; pass None-equivalent.
    assert _tokenize("   ") == []


def test_reciprocal_rank_fusion_combines_two_rankings():
    """Two rankings agreeing → top-1 is the agreed-on doc."""
    a = ["requests", "httpx", "urllib3"]
    b = ["requests", "aiohttp", "httpie"]
    fused = reciprocal_rank_fusion([a, b])
    assert fused[0] == "requests"


def test_reciprocal_rank_fusion_disagreement():
    """Doc that ranks consistently across both lists wins over a single #1."""
    # 'shared' is #2 in both; 'a_top' is #1 in only one. RRF rewards the
    # consistent runner-up over the single-list winner once enough other
    # docs separate them.
    a = ["a_top", "shared", "a_only"]
    b = ["b_top", "shared", "b_only"]
    fused = reciprocal_rank_fusion([a, b])
    # Both #1 docs each score 1/(60+1); 'shared' scores 2/(60+2). 'shared'
    # wins because consistency across rankings beats single-list dominance.
    assert fused[0] == "shared"


def test_reciprocal_rank_fusion_uses_k_constant():
    """k must actually change the fused ranking, not just be plumbed through.

    ``solo`` is rank 1 in one list only (score 1/(k+1)); ``consistent`` is rank
    4 in both lists (score 2/(k+4)). At k=1 the curve is sharp, so the single
    top-rank wins; at k=60 it flattens, so the doc that appears in both lists
    wins. Asserting their *relative* order makes this tie-free regardless of
    the filler docs.
    """
    a = ["solo", "f1", "f2", "consistent"]
    b = ["g1", "g2", "g3", "consistent"]

    small_k = reciprocal_rank_fusion([a, b], k=1)
    big_k = reciprocal_rank_fusion([a, b], k=60)

    # Sharp curve (k=1): the solo top-rank outranks the cross-list doc.
    assert small_k.index("solo") < small_k.index("consistent")
    # Flat curve (k=60): consistency across lists wins instead — the order flips.
    assert big_k.index("consistent") < big_k.index("solo")
    # Default k is 60.
    assert big_k == reciprocal_rank_fusion([a, b])


def test_reciprocal_rank_fusion_default_k_matches_constant():
    a = ["x", "y"]
    b = ["y", "x"]
    assert reciprocal_rank_fusion([a, b]) == reciprocal_rank_fusion([a, b], k=DEFAULT_RRF_K)


def test_reciprocal_rank_fusion_weights_rebalance():
    """Heavily-weighted retriever's preference dominates."""
    # Equal weights → 'shared' wins (the disagreement test). With dense weight
    # set very high, dense's #1 should win even though it's only in dense.
    dense = ["dense_pick", "shared"]
    sparse = ["sparse_pick", "shared"]
    fused = reciprocal_rank_fusion([dense, sparse], weights=[10.0, 0.1])
    assert fused[0] == "dense_pick"


def test_reciprocal_rank_fusion_empty_rankings_returns_empty():
    """No input rankings → empty result."""
    assert reciprocal_rank_fusion([]) == []


def test_reciprocal_rank_fusion_all_empty_lists_returns_empty():
    """Rankings of all empty lists → empty result."""
    assert reciprocal_rank_fusion([[], [], []]) == []


def test_reciprocal_rank_fusion_missing_doc_still_surfaces():
    """A doc only in one ranking, but high, still surfaces in the fusion."""
    a = ["only_in_a", "shared", "filler"]
    b = ["shared", "only_in_b"]
    fused = reciprocal_rank_fusion([a, b])
    assert "only_in_a" in fused
    assert "only_in_b" in fused
    assert "shared" in fused


def test_reciprocal_rank_fusion_weight_length_mismatch_raises():
    with pytest.raises(ValueError, match="weights length"):
        reciprocal_rank_fusion([["a"], ["b"]], weights=[1.0])


def test_bm25_index_search_finds_name_match():
    """Name match dominates ranking — that's why we triplicate names."""
    names = ["requests", "httpx", "urllib3"]
    descs = [
        "HTTP for Humans",
        "Next-gen HTTP client",
        "HTTP library with thread-safe connection pooling",
    ]
    idx = BM25Index(names, descs)
    results = idx.search("requests")
    assert results[0] == "requests"


def test_bm25_index_search_finds_description_match():
    """Description tokens count even when the name has no overlap."""
    # 'requests' has 'HTTP' only via the description here; the others are
    # name-matches for the query 'http'. We want to ensure description
    # signal flows through even when the name doesn't overlap.
    names = ["requests", "boto3", "django"]
    descs = [
        "HTTP for Humans, the most popular HTTP client",
        "AWS SDK for Python",
        "Web framework",
    ]
    idx = BM25Index(names, descs)
    results = idx.search("http client")
    # 'requests' should appear because of description tokens, even though
    # its name doesn't include 'http' or 'client'.
    assert "requests" in results


def test_bm25_index_search_empty_query_returns_empty():
    idx = BM25Index(["a", "b"], ["x", "y"])
    assert idx.search("") == []


def test_bm25_index_search_zero_score_filtered():
    """Out-of-vocabulary query yields [], not the corpus in arbitrary order."""
    idx = BM25Index(["requests", "httpx"], ["HTTP for Humans", "HTTP client"])
    assert idx.search("kubernetes orchestration") == []


def test_bm25_index_search_respects_top_k():
    names = [f"pkg_{i}" for i in range(10)]
    descs = ["http library"] * 10
    idx = BM25Index(names, descs)
    results = idx.search("http", k=3)
    assert len(results) == 3


def test_bm25_index_init_mismatched_lengths_raises():
    with pytest.raises(ValueError, match="length"):
        BM25Index(["a", "b"], ["only-one-desc"])


def test_bm25_index_handles_empty_corpus():
    idx = BM25Index([], [])
    assert len(idx) == 0
    assert idx.search("anything") == []


def test_bm25_index_handles_doc_with_empty_text():
    """A doc whose name and description both tokenize to nothing.

    BM25Okapi's IDF goes to 0 when a term appears in >50% of docs; for tiny
    test corpora that means most queries score zero. This test only verifies
    the index builds, has the right length, and search returns a list rather
    than crashing on a sentinel-substituted empty doc.
    """
    idx = BM25Index(["!!!", "requests"], ["", "HTTP for Humans"])
    assert len(idx) == 2
    # Search must not raise; result list may be empty due to IDF math on a
    # 2-doc corpus, but the empty-doc case is the thing under test.
    assert isinstance(idx.search("http"), list)


def test_bm25_index_len_reports_corpus_size():
    idx = BM25Index(["a", "b", "c"], ["x", "y", "z"])
    assert len(idx) == 3


def test_rrf_prior_weight_zero_is_noop():
    """prior_weight=0 (or no priors) must leave the fusion untouched."""
    rankings = [["a", "b", "c"], ["c", "b", "a"]]
    base = reciprocal_rank_fusion(rankings)
    assert reciprocal_rank_fusion(rankings, priors={"a": 1.0}, prior_weight=0.0) == base
    assert reciprocal_rank_fusion(rankings, priors=None, prior_weight=0.9) == base


def test_rrf_prior_reranks_toward_high_prior():
    """A strong prior lifts a fusion-mediocre doc above the RRF leader."""
    rankings = [["a", "b", "c"], ["a", "b", "c"]]  # 'a' leads RRF, 'c' trails
    assert reciprocal_rank_fusion(rankings)[0] == "a"
    boosted = reciprocal_rank_fusion(
        rankings, priors={"c": 1.0, "a": 0.0, "b": 0.0}, prior_weight=0.9
    )
    assert boosted[0] == "c"  # popularity prior overcomes the small RRF lead


def test_rrf_prior_ignores_docs_not_in_fusion():
    """A prior for a doc neither retriever surfaced must not inject it."""
    fused = reciprocal_rank_fusion([["a", "b"]], priors={"zzz": 1.0}, prior_weight=0.5)
    assert "zzz" not in fused


def test_rrf_prior_zero_span_orders_by_prior():
    """When all RRF scores tie (zero span), the prior alone decides order."""
    # two single-doc rankings → both at rank 0 → identical RRF score.
    fused = reciprocal_rank_fusion(
        [["a"], ["b"]], weights=[1.0, 1.0], priors={"a": 1.0, "b": 0.0}, prior_weight=0.5
    )
    assert fused == ["a", "b"]
