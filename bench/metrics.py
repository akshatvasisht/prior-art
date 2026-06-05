"""
Minimal IR metrics for the priorart retrieval benchmark.

We compute the same three metrics BEIR reports — nDCG@k, Recall@k, MRR — but
keep the implementation inline so the benchmark can run without pulling the
full BEIR dependency tree. Swap in ``beir.retrieval.evaluation.EvaluateRetrieval``
when/if we start using BEIR's shared corpora.

Relevance is graded ({1, 2}): top awesome-list entries within a category are
treated as the maintainer's "top picks" (grade=2) and the rest as still-relevant
(grade=1). nDCG uses the standard ``(2^rel - 1) / log2(i + 2)`` gain. Recall
and MRR remain binary — a doc with grade>=1 is "relevant".
"""

from __future__ import annotations

import math
from collections.abc import Mapping


def _dedupe(ranked: list[str]) -> list[str]:
    """Drop repeat doc IDs, keeping first occurrence. A ranked list with the
    same doc twice would otherwise let recall/nDCG count it twice and exceed 1.0."""
    return list(dict.fromkeys(ranked))


def ndcg_at_k(relevant: Mapping[str, int], ranked: list[str], k: int) -> float:
    """Graded nDCG@k. ``relevant`` maps doc-name -> grade in {1, 2, ...}."""
    ranked = _dedupe(ranked)
    dcg = 0.0
    for i, name in enumerate(ranked[:k]):
        grade = relevant.get(name, 0)
        if grade > 0:
            dcg += (2**grade - 1) / math.log2(i + 2)
    # Ideal DCG places the highest-grade docs first — sort grades descending.
    ideal_grades = sorted(relevant.values(), reverse=True)[:k]
    idcg = sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(ideal_grades))
    return dcg / idcg if idcg else 0.0


def recall_at_k(relevant: Mapping[str, int], ranked: list[str], k: int) -> float:
    """Binary recall@k: any doc with grade>=1 in the top-k counts as a hit."""
    if not relevant:
        return 0.0
    ranked = _dedupe(ranked)
    hits = sum(1 for name in ranked[:k] if relevant.get(name, 0) > 0)
    return hits / len(relevant)


def reciprocal_rank(relevant: Mapping[str, int], ranked: list[str]) -> float:
    """Binary MRR: 1/(rank of first grade>=1 hit), 0 if none."""
    for i, name in enumerate(ranked):
        if relevant.get(name, 0) > 0:
            return 1.0 / (i + 1)
    return 0.0


def aggregate(per_query: list[dict]) -> dict[str, float]:
    """Mean each metric across queries. Ignores queries with empty ranked lists."""
    if not per_query:
        return {}
    keys = per_query[0].keys()
    return {k: sum(q[k] for q in per_query) / len(per_query) for k in keys}
