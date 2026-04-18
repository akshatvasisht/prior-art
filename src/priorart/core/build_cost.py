"""
Build-vs-borrow lens.

Heuristic estimator for three fields attached to every ScoredPackage:
  * build_cost_weeks — rough engineer-weeks to reimplement the package's public API
  * commodity_tag    — "commodity" vs "differentiator"
  * maintenance_liability — "low" | "medium" | "high"

All three are calibrated best-effort heuristics and should be surfaced to
the user as estimates, not ground truth. Inputs available at score time:
package description, direct dependency count, major-version cadence,
reverse-dependency count, and (optionally) ingestion signals if the caller
has run ingest_repo.
"""

from __future__ import annotations

import re
from typing import Any

# Category keywords that almost always indicate commodity functionality:
# widely reimplemented, no strategic advantage to custom build.
_COMMODITY_KEYWORDS = frozenset(
    {
        "http",
        "https",
        "rest",
        "client",
        "json",
        "yaml",
        "toml",
        "xml",
        "logging",
        "logger",
        "log",
        "serialization",
        "serializer",
        "parser",
        "parse",
        "auth",
        "authentication",
        "authorization",
        "jwt",
        "oauth",
        "testing",
        "mock",
        "assert",
        "fixture",
        "retry",
        "backoff",
        "cache",
        "caching",
        "rate-limit",
        "throttle",
        "uuid",
        "hash",
        "base64",
        "encoding",
        "date",
        "datetime",
        "timezone",
    }
)


def _classify_commodity(description: str | None, package_name: str | None) -> str:
    """Return 'commodity' or 'differentiator'."""
    tokens = set()
    for source in (description, package_name):
        if not source:
            continue
        tokens.update(re.findall(r"[a-zA-Z\-]{3,}", source.lower()))
    if tokens & _COMMODITY_KEYWORDS:
        return "commodity"
    return "differentiator"


def _estimate_weeks(data: dict[str, Any]) -> float:
    """Rough engineer-weeks to reimplement the package's public API.

    Uses ingest signals if present (public API LoC + symbol count), falls back
    to dependency count + description length as a coarse proxy.

    Formula keeps the estimate bounded between 0.5 and 52 weeks to avoid
    nonsense outputs for packages with little signal.
    """
    api_loc = data.get("api_loc") or 0
    symbols = data.get("public_symbols") or 0
    deps = data.get("direct_dep_count") or 0

    if api_loc or symbols:
        weeks = 0.5 * (api_loc / 1000.0) + 0.3 * deps + 0.2 * (symbols / 50.0)
    else:
        # Fallback: description length + deps as a proxy
        desc_len = len(data.get("description") or "")
        weeks = 0.5 + 0.2 * deps + 0.4 * (desc_len / 500.0)

    return max(0.5, min(52.0, round(weeks, 1)))


def _maintenance_liability(data: dict[str, Any]) -> str:
    """low | medium | high based on release cadence in this space."""
    mvpy = data.get("major_versions_per_year") or 0.0
    release_cv = data.get("release_cv")

    # High = fast-moving ecosystem, frequent majors
    if mvpy >= 1.5:
        return "high"
    if mvpy >= 0.5:
        return "medium"
    if release_cv is not None and release_cv > 1.5:
        return "medium"
    return "low"


def enrich_build_vs_borrow(scored: Any, package_data: dict[str, Any]) -> None:
    """Attach BVB fields to a ScoredPackage in place."""
    scored.build_cost_weeks = _estimate_weeks(package_data)
    scored.commodity_tag = _classify_commodity(
        package_data.get("description"), package_data.get("package_name")
    )
    scored.maintenance_liability = _maintenance_liability(package_data)
