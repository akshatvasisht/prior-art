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
import subprocess
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from priorart.core.registry import PackageCandidate, get_registry_client
from priorart.core.retrieval import retrieve_candidates

from .metrics import aggregate, ndcg_at_k, recall_at_k, reciprocal_rank

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


def evaluate(gold: list[dict], k: int, baselines: list[str]) -> dict[str, dict[str, float]]:
    """Run the configured baselines over ``gold`` and return mean metrics per baseline."""
    results: dict[str, list[dict]] = {b: [] for b in baselines}

    for item in gold:
        query = item["query"]
        language = item["language"]
        # graded form (`relevant_grades` -> dict). Older fixtures with a
        # bare `relevant` list still parse — every doc gets grade=1 so binary
        # behavior is preserved.
        if "relevant_grades" in item:
            relevant = dict(item["relevant_grades"])
        else:
            relevant = {name: 1 for name in item.get("relevant", [])}

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

    # One JSON envelope (meta + results) makes downstream parsing trivial:
    # `jq .meta`, `jq .results.overall`. Splitting into two prints would
    # force consumers to handle a multi-document stream.
    output = {
        "meta": build_meta(args.fixture),
        "results": evaluate_stratified(gold, args.k, baselines),
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
