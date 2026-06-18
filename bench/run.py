"""
Retrieval benchmark for priorart.

Evaluates the semantic retriever against a gold-standard of (query, language,
relevant package list). Ships with a small fixture in ``bench/fixtures/`` so
the scaffold is runnable before the hosted index exists; the full BEIR-style
run will expand the gold standard from awesome-lists + Stack Overflow.

Baselines:
- ``semantic``: the v0.2 semantic retriever (usearch + bge-small).
- ``registry``: live registry keyword search (pre-v0.2 behavior).

Output is a single JSON document with shape ``{"meta": {...}, "results": {...}}``.
``meta`` captures index/embed model/git SHAs so a run can be reproduced;
``results`` is stratified per-language with a final ``overall`` row, so
regressions in one ecosystem don't get hidden inside the cross-language mean.

Usage::

    python -m bench.run --fixture bench/fixtures/gold_standard.jsonl --k 10
"""

from __future__ import annotations

import argparse
import json
import logging
import resource
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from priorart.core.registry import PackageCandidate, get_registry_client
from priorart.core.retrieval import (
    HYBRID_POOL,
    _ecosystem_for,
    _retriever_for,
    retrieve_candidates,
)

from .metrics import (
    aggregate,
    condensed_ndcg_at_k,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    success_at_k,
)

logger = logging.getLogger(__name__)


def _load_gold(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _canonical_name(name: str, language: str) -> str:
    # Defense-in-depth: even if retrieval.py forgets to strip Go module paths
    # ("github.com/owner/repo"), the benchmark still scores against bare slugs
    # so gold-standard repo names line up with retrieved candidate names.
    if language == "go":
        return name.rsplit("/", 1)[-1].lower()
    return name.lower()


def _semantic_ranked(query: str, language: str, k: int) -> list[str]:
    cands = retrieve_candidates(query, language, max_results=k)
    return [_canonical_name(c.name, language) for c in cands]


def _registry_ranked(query: str, language: str, k: int) -> list[str]:
    with get_registry_client(language) as client:
        cands: list[PackageCandidate] = client.search(query, max_results=k)
    return [_canonical_name(c.name, language) for c in cands]


BASELINES: dict[str, Callable[[str, str, int], list[str]]] = {
    "semantic": _semantic_ranked,
    "registry": _registry_ranked,
}


def _relevant_set(item: dict, language: str) -> dict[str, int]:
    """Canonicalized relevant-name → grade map for a gold item.

    Graded form (`relevant_grades`) preferred; older fixtures with a bare
    `relevant` list map every doc to grade 1 (binary behavior preserved). Keys are
    canonicalized with the same `_canonical_name` as retrieved names, so a
    mixed-case fixture entry ("Requests") still matches a candidate ("requests").
    """
    if "relevant_grades" in item:
        return {
            _canonical_name(name, language): grade
            for name, grade in item["relevant_grades"].items()
        }
    return {_canonical_name(name, language): 1 for name in item.get("relevant", [])}


def evaluate(gold: list[dict], k: int, baselines: list[str]) -> dict[str, dict[str, float]]:
    """Run the configured baselines over ``gold`` and return mean metrics per baseline."""
    results: dict[str, list[dict]] = {b: [] for b in baselines}

    for item in gold:
        query = item["query"]
        language = item["language"]
        relevant = _relevant_set(item, language)

        for baseline in baselines:
            try:
                ranked = BASELINES[baseline](query, language, k)
            except Exception as e:
                logger.warning(f"{baseline} failed on '{query}/{language}': {e}")
                ranked = []

            results[baseline].append(
                {
                    # Incompleteness-robust headline metrics (positives-only,
                    # single-pool gold) lead; raw nDCG@k stays as a diagnostic.
                    f"cnd_ndcg@{k}": condensed_ndcg_at_k(relevant, ranked, k),
                    "success@5": success_at_k(relevant, ranked, 5),
                    f"ndcg@{k}": ndcg_at_k(relevant, ranked, k),
                    f"recall@{k}": recall_at_k(relevant, ranked, k),
                    "mrr": reciprocal_rank(relevant, ranked),
                }
            )

    return {b: aggregate(per_query) for b, per_query in results.items()}


def evaluate_stratified(
    gold: list[dict], k: int, baselines: list[str]
) -> dict[str, dict[str, dict[str, float]]]:
    """Per-language slices first, ``overall`` last.

    Per-language regressions get washed out in a single cross-language mean
    (e.g. a 30% drop on Go can be invisible if Python carries the average),
    so we report each ecosystem's slice and keep the aggregate as a footer.
    """
    by_language: dict[str, list[dict]] = defaultdict(list)
    for item in gold:
        by_language[item["language"]].append(item)

    out: dict[str, dict[str, dict[str, float]]] = {}
    # Sorted for stable output ordering — easier to diff across runs.
    for language in sorted(by_language):
        out[language] = evaluate(by_language[language], k, baselines)
    out["overall"] = evaluate(gold, k, baselines)
    return out


def _pool_z_stats(sims: list[float]) -> tuple[float, float, float, float | None]:
    """``(top1, mean, std, z_top1)`` for a descending similarity pool.

    ``z_top1`` is ``None`` for a degenerate (zero-variance) pool. Mirrors the gate
    in ``retrieval._should_fall_back`` so calibration measures the same quantity.
    """
    top1 = sims[0]
    mean = sum(sims) / len(sims)
    std = (sum((s - mean) ** 2 for s in sims) / len(sims)) ** 0.5
    z = (top1 - mean) / std if std > 1e-9 else None
    return top1, mean, std, z


def floor_stats(gold: list[dict], k: int) -> dict[str, list[dict]]:
    """Per-query dense-pool stats grouped by language, to calibrate ``relevance_z_floor``.

    For each gold query records the dense pool's top-1 similarity, mean, std,
    z-score, and whether a relevant doc is in the pool. Comparing z for
    relevant-present vs relevant-absent queries gives the per-ecosystem z-floor
    that keeps confident queries while triggering fallback on the rest
    (OPEN_ISSUES A20/A22). Needs the shards present (run against a built index).
    """
    pool_k = max(HYBRID_POOL, k)
    out: dict[str, list[dict]] = defaultdict(list)
    for item in gold:
        language = item["language"]
        relevant = set(_relevant_set(item, language))
        try:
            retriever = _retriever_for(_ecosystem_for(language))
            hits = retriever.search_dense(item["query"], k=pool_k)
        except Exception as e:
            logger.warning(f"floor-stats failed for '{item['query']}/{language}': {e}")
            continue
        if not hits:
            out[language].append(
                {"query": item["query"], "n": 0, "z": None, "relevant_in_pool": False}
            )
            continue
        sims = [h.similarity for h in hits]
        top1, mean, std, z = _pool_z_stats(sims)
        names = {_canonical_name(h.name, language) for h in hits}
        out[language].append(
            {
                "query": item["query"],
                "n": len(sims),
                "top1": round(top1, 4),
                "mean": round(mean, 4),
                "std": round(std, 4),
                "z": round(z, 3) if z is not None else None,
                "relevant_in_pool": bool(names & relevant),
            }
        )
    return dict(out)


def _git_rev_parse(ref: str) -> str:
    """Return ``git rev-parse <ref>`` or 'unknown' if git fails (not in repo, missing path)."""
    try:
        return (
            subprocess.check_output(["git", "rev-parse", ref], stderr=subprocess.DEVNULL)
            .decode()
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _index_manifest_version() -> str:
    """Look up the manifest version without re-downloading the shard.

    We avoid ``ensure_manifest()`` because it triggers a HF Hub round-trip and
    sigstore verification — far too expensive for a metadata preamble.
    Instead we read the cached manifest file directly. If it isn't there,
    fall back to "unknown" rather than failing the whole bench run.
    """
    try:
        from priorart.core.index_download import index_dir
    except ImportError:
        return "unknown"

    manifest_path = index_dir() / "manifest.json"
    if not manifest_path.exists():
        return "unknown"
    try:
        version = json.loads(manifest_path.read_text()).get("version", "unknown")
    except (json.JSONDecodeError, OSError):
        return "unknown"
    return str(version)


def _embed_model_name() -> str:
    try:
        from priorart.core.retrieval import EMBED_MODEL_NAME

        return str(EMBED_MODEL_NAME)
    except ImportError:
        return "unknown"


def build_meta(fixture_path: Path) -> dict[str, Any]:
    """Reproducibility preamble. Anything in the bench output traces back to these IDs."""
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "priorart_git_sha": _git_rev_parse("HEAD"),
        "gold_standard_sha": _git_rev_parse(f"HEAD:{fixture_path}"),
        "index_manifest_version": _index_manifest_version(),
        "embed_model_name": _embed_model_name(),
        "fixture_path": str(fixture_path),
    }


def _peak_rss_mb() -> float:
    """Peak resident memory of this process in MB.

    ``ru_maxrss`` is reported in KB on Linux but in bytes on macOS/BSD, so the
    conversion to MB is platform-dependent.

    Surfaced so a memory regression — e.g. loading all six ecosystems' shards +
    BM25 indices after a top_n bump — shows up in bench output rather than only
    at OOM time.
    """
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    mb = raw / 1024 if sys.platform.startswith("linux") else raw / 1024**2
    return round(mb, 1)


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
    parser.add_argument(
        "--floor-stats",
        action="store_true",
        help="emit per-query dense-pool z-stats for relevance_z_floor calibration, then exit",
    )
    args = parser.parse_args()

    gold = _load_gold(args.fixture)

    if args.floor_stats:
        meta = build_meta(args.fixture)
        print(json.dumps({"meta": meta, "floor_stats": floor_stats(gold, args.k)}, indent=2))
        return

    baselines = [b.strip() for b in args.baselines.split(",") if b.strip() in BASELINES]

    # One JSON envelope (meta + results) makes downstream parsing trivial:
    # `jq .meta`, `jq .results.overall`. Splitting into two prints would
    # force consumers to handle a multi-document stream.
    # Evaluate first so peak RSS captures the shards + BM25 indices loaded
    # during retrieval, not just the pre-load baseline.
    results = evaluate_stratified(gold, args.k, baselines)
    meta = build_meta(args.fixture)
    meta["peak_rss_mb"] = _peak_rss_mb()
    output = {"meta": meta, "results": results}
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
