"""
Per-package evaluation — `priorart inspect <package>`.

Skip retrieval. Run the signal pipeline + scoring + build-vs-borrow lens
directly on a known (name, registry) pair. Used for the `inspect` CLI
command and the `evaluate_package` MCP tool.
"""

from __future__ import annotations

import logging
from typing import Any

from .cache import SQLiteCache
from .find_alternatives import evaluate_candidate
from .registry import get_registry_client
from .scoring import PackageScorer
from .utils import load_config

logger = logging.getLogger(__name__)


_LANG_FOR_REGISTRY = {
    "pypi": "python",
    "npm": "javascript",
    "cargo": "rust",
    "go": "go",
}


def _infer_registry(language: str | None, package_name: str) -> tuple[str, str]:
    """Return (language, registry) pair, inferring from the package name shape if needed."""
    if language:
        lang = language.lower()
        registry_for_lang = {
            "python": "pypi",
            "javascript": "npm",
            "typescript": "npm",
            "node": "npm",
            "rust": "cargo",
            "cargo": "cargo",
            "go": "go",
            "golang": "go",
        }
        registry = registry_for_lang.get(lang)
        if not registry:
            raise ValueError(f"Unsupported language: {language}")
        return lang, registry

    # Heuristic inference
    if package_name.startswith("github.com/") or package_name.startswith("golang.org/"):
        return "go", "go"
    if package_name.startswith("@") or "/" in package_name:
        return "javascript", "npm"
    # Default to PyPI for bare names
    return "python", "pypi"


def inspect_package(
    package_name: str, language: str | None = None, explain: bool = False
) -> dict[str, Any]:
    """Evaluate a single named package end-to-end.

    Args:
        package_name: Name as it appears on the registry (e.g. "requests", "@tanstack/query").
        language: Optional language hint. Inferred from name shape if omitted.
        explain: Include the scoring breakdown.

    Returns:
        Dict matching the find_alternatives result shape for a single package,
        or a structured error response.
    """
    try:
        lang, _ = _infer_registry(language, package_name)
        config = load_config()
        cache = SQLiteCache()
        scorer = PackageScorer(config)

        with get_registry_client(lang) as client:
            candidate = client.get_package_info(package_name)

        if candidate is None:
            return {
                "status": "not_found",
                "message": f"Package '{package_name}' not found on the {lang} registry.",
            }

        scored = evaluate_candidate(candidate, lang, cache, config, scorer, explain=explain)
        if scored is None:
            return {
                "status": "no_signals",
                "message": (
                    f"Could not collect signals for '{package_name}'. "
                    "The package may have no linked GitHub repository."
                ),
            }

        package_dict: dict[str, Any] = {
            "name": scored.name,
            "full_name": scored.full_name,
            "url": scored.url,
            "package_name": scored.package_name,
            "registry": scored.registry,
            "description": scored.description,
            "health_score": scored.health_score,
            "recommendation": scored.recommendation,
            "identity_verified": scored.identity_verified,
            "age_years": round(scored.age_years, 1),
            "weekly_downloads": scored.weekly_downloads,
            "license": scored.license,
            "license_warning": scored.license_warning,
            "dep_health_flag": scored.dep_health_flag,
            "likely_abandoned": scored.likely_abandoned,
            "scorecard_overall": scored.scorecard_overall,
            "build_cost_weeks": scored.build_cost_weeks,
            "commodity_tag": scored.commodity_tag,
            "maintenance_liability": scored.maintenance_liability,
        }

        if explain and scored.score_breakdown:
            package_dict["score_breakdown"] = {
                "reliability": scored.score_breakdown.reliability,
                "adoption": scored.score_breakdown.adoption,
                "versioning": scored.score_breakdown.versioning,
                "activity_regularity": scored.score_breakdown.activity_regularity,
                "dependency_health": scored.score_breakdown.dependency_health,
            }

        return {"status": "success", "package": package_dict}

    except Exception as e:
        logger.error(f"Error inspecting {package_name}: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
