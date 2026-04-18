"""Tests for build_cost heuristics (commodity tag, engineer-weeks, liability)."""

from priorart.core.build_cost import (
    _classify_commodity,
    _estimate_weeks,
    _maintenance_liability,
    enrich_build_vs_borrow,
)


class _Scored:
    """Minimal stand-in for ScoredPackage used by enrich_build_vs_borrow."""

    build_cost_weeks: float | None = None
    commodity_tag: str | None = None
    maintenance_liability: str | None = None


# --- _classify_commodity ---


def test_classify_commodity_none_sources():
    """Both description and package_name None returns 'differentiator' (no tokens)."""
    # Covers the `continue` branch when source is falsy.
    assert _classify_commodity(None, None) == "differentiator"


def test_classify_commodity_differentiator():
    """Description with no commodity keywords returns 'differentiator'."""
    assert _classify_commodity("machine learning model orchestrator", "mypkg") == "differentiator"


def test_classify_commodity_detects_http():
    """Known commodity keyword classifies as 'commodity'."""
    assert _classify_commodity("simple http client library", "httpx") == "commodity"


# --- _estimate_weeks ---


def test_estimate_weeks_with_api_signals():
    """When api_loc / public_symbols are present, use the ingest-signal formula."""
    weeks = _estimate_weeks({"api_loc": 2000, "public_symbols": 100, "direct_dep_count": 3})
    # 0.5 * 2 + 0.3 * 3 + 0.2 * 2 = 1.0 + 0.9 + 0.4 = 2.3
    assert weeks == 2.3


def test_estimate_weeks_fallback_without_api_signals():
    """Without ingest signals, falls back to desc-length + deps."""
    weeks = _estimate_weeks({"description": "x" * 500, "direct_dep_count": 5})
    # 0.5 + 0.2 * 5 + 0.4 * 1.0 = 1.9
    assert weeks == 1.9


# --- _maintenance_liability ---


def test_maintenance_liability_high():
    """mvpy >= 1.5 classifies as 'high'."""
    assert _maintenance_liability({"major_versions_per_year": 2.0}) == "high"


def test_maintenance_liability_medium():
    """mvpy between 0.5 and 1.5 classifies as 'medium'."""
    assert _maintenance_liability({"major_versions_per_year": 0.8}) == "medium"


def test_maintenance_liability_high_cv():
    """release_cv > 1.5 (with low mvpy) classifies as 'medium'."""
    assert _maintenance_liability({"major_versions_per_year": 0.1, "release_cv": 2.0}) == "medium"


def test_maintenance_liability_low():
    """Stable ecosystem classifies as 'low'."""
    assert _maintenance_liability({"major_versions_per_year": 0.1, "release_cv": 0.3}) == "low"


# --- enrich_build_vs_borrow ---


def test_enrich_sets_all_three_fields():
    scored = _Scored()
    enrich_build_vs_borrow(
        scored,
        {
            "description": "HTTP client",
            "package_name": "httpx",
            "direct_dep_count": 2,
            "major_versions_per_year": 0.1,
        },
    )
    assert scored.commodity_tag == "commodity"
    assert scored.maintenance_liability == "low"
    assert scored.build_cost_weeks is not None
