"""
BM25 + dense retrieval fusion via Reciprocal Rank Fusion.

Addresses a known weakness: dense retrievers favor lexical overlap over semantic relevance
(Reichman & Heck 2025, arxiv 2503.05037). Hybrid fusion gives keyword-friendly
queries a sparse-retrieval safety net, and lets dense retrieval handle
semantic-only queries that BM25 misses. RRF at k=60 combines two ranked lists
purely on rank order, sidestepping score-scale mismatches between BM25 (>100)
and cosine similarity (-1 to 1).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from rank_bm25 import BM25Okapi

# Lowercase alphanumeric runs only. No stemming, no stopwords — the corpus is
# package metadata (~20K short docs per ecosystem) where rare technical tokens
# carry the discriminative signal; aggressive normalization would erase it.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

# Field-aware BM25 proxy: repeat package names this many times in the
# tokenized doc. Names are short, high-precision; descriptions are long and
# noisy. Triplicating the name biases the score toward exact name matches
# without the bookkeeping of true field-aware BM25F.
_NAME_REPEAT = 3

# Cormack et al. (2009) RRF default. Lower k sharpens the curve toward
# top-ranked docs; higher k flattens it. 60 is the literature standard and
# what the original paper recommends out of the box.
DEFAULT_RRF_K = 60


def _tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric runs only — no stemming, no stopwords.

    Stemming hurts package retrieval: ``request`` vs. ``requests`` are
    different libraries. Stopword removal would drop tokens like ``http`` and
    ``go`` that are core to many package names.
    """
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")]


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]],
    k: int = DEFAULT_RRF_K,
    weights: Sequence[float] | None = None,
) -> list[str]:
    """Fuse multiple ranked lists into one via Reciprocal Rank Fusion.

    Score per doc d:  sum over rankings r of  ``w_r / (k + rank_r(d))``,
    where ``rank_r(d)`` is the 1-indexed position of d in ranking r (omitted
    if absent). Ties are broken by insertion order, which mirrors Python's
    stable sort.

    Args:
        rankings: Sequence of ranked lists. Each list is doc identifiers in
            descending relevance.
        k: RRF damping constant. See module docstring.
        weights: Per-ranking weight. Defaults to uniform 1.0.

    Returns:
        Fused ranking (descending). Empty if no input rankings yield any docs.

    Raises:
        ValueError: If ``weights`` length disagrees with ``rankings`` length.
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError(f"weights length {len(weights)} != rankings length {len(rankings)}")

    scores: dict[str, float] = {}
    for rank_list, weight in zip(rankings, weights, strict=True):
        for i, doc in enumerate(rank_list):
            scores[doc] = scores.get(doc, 0.0) + weight / (k + i + 1)

    # Stable sort preserves first-seen order on ties — meaningful when two
    # docs only appear in one ranking each at identical positions.
    return sorted(scores.keys(), key=lambda d: scores[d], reverse=True)


class BM25Index:
    """Per-corpus BM25 index over (name, description) tuples.

    Built eagerly at construction time, then immutable. Designed for
    in-memory use over the ecosystem's metadata sidecar (~20K docs per
    shard); tokenization runs once on init and search is then a single
    BM25Okapi.get_scores call (~10ms on 20K docs).
    """

    def __init__(self, names: Sequence[str], descriptions: Sequence[str]):
        if len(names) != len(descriptions):
            raise ValueError(
                f"names length {len(names)} != descriptions length {len(descriptions)}"
            )
        self._names: list[str] = list(names)
        # 3x name-repeat biases scoring toward exact name matches; cheap proxy
        # for true field-aware BM25F (which rank-bm25 doesn't ship).
        tokenized = [
            _tokenize(name) * _NAME_REPEAT + _tokenize(desc)
            for name, desc in zip(names, descriptions, strict=True)
        ]
        # BM25Okapi requires a non-empty corpus and chokes on docs that
        # tokenize to []. Replace empty docs with a sentinel so the index
        # still builds; scoring against a sentinel yields ~0 in practice.
        tokenized = [doc if doc else ["__empty__"] for doc in tokenized]
        self._bm25 = BM25Okapi(tokenized) if tokenized else None

    def search(self, query: str, k: int = 50) -> list[str]:
        """Return up to ``k`` names by BM25 score, descending.

        Filters zero-score docs so an out-of-vocabulary query yields ``[]``
        rather than the corpus in arbitrary order.
        """
        if self._bm25 is None:
            return []
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        scores = self._bm25.get_scores(query_tokens)
        # Python sort over ~20K floats is ~5ms; the constant-factor win from
        # numpy.argpartition isn't worth the readability cost here.
        idx_sorted = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [self._names[i] for i in idx_sorted[:k] if scores[i] > 0]

    def __len__(self) -> int:
        return len(self._names)
