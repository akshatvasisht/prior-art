"""
Minimal IR metrics for the priorart retrieval benchmark.

We compute the same three metrics BEIR reports — nDCG@k, Recall@k, MRR — but
keep the implementation inline so the benchmark can run without pulling the
full BEIR dependency tree. Swap in ``beir.retrieval.evaluation.EvaluateRetrieval``
when/if we start using BEIR's shared corpora.
"""

from __future__ import annotations

import math
from collections.abc import Iterable


def ndcg_at_k(relevant: Iterable[str], ranked: list[str], k: int) -> float:
    rel_set = set(relevant)
    dcg = 0.0
    for i, name in enumerate(ranked[:k]):
        if name in rel_set:
            dcg += 1.0 / math.log2(i + 2)
    # Ideal DCG assumes all relevant items ranked first (binary relevance).
    ideal_hits = min(len(rel_set), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg else 0.0


def recall_at_k(relevant: Iterable[str], ranked: list[str], k: int) -> float:
    rel_set = set(relevant)
    if not rel_set:
        return 0.0
    return len(rel_set.intersection(ranked[:k])) / len(rel_set)


def reciprocal_rank(relevant: Iterable[str], ranked: list[str]) -> float:
    rel_set = set(relevant)
    for i, name in enumerate(ranked):
        if name in rel_set:
            return 1.0 / (i + 1)
    return 0.0


def aggregate(per_query: list[dict]) -> dict[str, float]:
    """Mean each metric across queries. Ignores queries with empty ranked lists."""
    if not per_query:
        return {}
    keys = per_query[0].keys()
    return {k: sum(q[k] for q in per_query) / len(per_query) for k in keys}
