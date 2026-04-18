"""
Retrieval benchmark for priorart.

Evaluates the semantic retriever against a gold-standard of (query, language,
relevant package list). Ships with a small fixture in ``bench/fixtures/`` so
the scaffold is runnable before the hosted index exists; the full BEIR-style
run will expand the gold standard from awesome-lists + Stack Overflow.

Baselines:
- ``semantic``: the v0.2 semantic retriever (usearch + bge-small).
- ``registry``: live registry keyword search (pre-v0.2 behavior).

Usage::

    python -m bench.run --fixture bench/fixtures/gold_standard.jsonl --k 10
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from priorart.core.registry import PackageCandidate, get_registry_client
from priorart.core.retrieval import retrieve_candidates

from .metrics import aggregate, ndcg_at_k, recall_at_k, reciprocal_rank

logger = logging.getLogger(__name__)


def _load_gold(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _semantic_ranked(query: str, language: str, k: int) -> list[str]:
    cands = retrieve_candidates(query, language, max_results=k)
    return [c.name for c in cands]


def _registry_ranked(query: str, language: str, k: int) -> list[str]:
    with get_registry_client(language) as client:
        cands: list[PackageCandidate] = client.search(query, max_results=k)
    return [c.name for c in cands]


BASELINES = {
    "semantic": _semantic_ranked,
    "registry": _registry_ranked,
}


def evaluate(gold: list[dict], k: int, baselines: list[str]) -> dict[str, dict[str, float]]:
    results: dict[str, list[dict]] = {b: [] for b in baselines}

    for item in gold:
        query = item["query"]
        language = item["language"]
        relevant = item["relevant"]

        for baseline in baselines:
            try:
                ranked = BASELINES[baseline](query, language, k)
            except Exception as e:
                logger.warning(f"{baseline} failed on '{query}/{language}': {e}")
                ranked = []

            results[baseline].append(
                {
                    f"ndcg@{k}": ndcg_at_k(relevant, ranked, k),
                    f"recall@{k}": recall_at_k(relevant, ranked, k),
                    "mrr": reciprocal_rank(relevant, ranked),
                }
            )

    return {b: aggregate(per_query) for b, per_query in results.items()}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=Path("bench/fixtures/gold_standard.jsonl"))
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument(
        "--baselines",
        default="semantic,registry",
        help="comma-separated subset of: semantic, registry",
    )
    args = parser.parse_args()

    gold = _load_gold(args.fixture)
    baselines = [b.strip() for b in args.baselines.split(",") if b.strip() in BASELINES]

    agg = evaluate(gold, args.k, baselines)
    print(json.dumps(agg, indent=2))


if __name__ == "__main__":
    main()
