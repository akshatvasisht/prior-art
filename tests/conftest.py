"""Pytest configuration and shared fixtures."""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from priorart.core.cache import SignalSnapshot


@pytest.fixture
def temp_cache_dir():
    """Create a temporary directory for cache during tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_github_token():
    """Set a mock GitHub token for tests."""
    old_token = os.environ.get("GITHUB_TOKEN")
    os.environ["GITHUB_TOKEN"] = "test_token_12345"
    yield "test_token_12345"
    if old_token:
        os.environ["GITHUB_TOKEN"] = old_token
    else:
        os.environ.pop("GITHUB_TOKEN", None)


@pytest.fixture
def sample_package_snapshot():
    """Create a sample package snapshot for testing."""
    return SignalSnapshot(
        package_name="requests",
        registry="pypi",
        github_url="https://github.com/psf/requests",
        identity_verified=True,
        weekly_downloads=45000000,
        star_count=50000,
        fork_count=9000,
        fork_to_star_ratio=0.18,
        days_since_last_commit=5,
        open_issue_count=250,
        closed_issues_last_year=800,
        mttr_median_days=7.5,
        mttr_mad=3.2,
        mttr_state="measured",
        weekly_commit_cv=0.25,
        recent_committer_count=25,
        latest_version="2.31.0",
        first_release_date=datetime(2011, 2, 13, tzinfo=timezone.utc),
        release_cv=0.3,
        major_versions_per_year=0.15,
        direct_dep_count=5,
        vulnerable_dep_count=0,
        deprecated_dep_count=0,
        reverse_dep_count=150000,
        description="Python HTTP for Humans",
        license="Apache-2.0",
        created_at=datetime.now(timezone.utc) - timedelta(days=30),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_package_data():
    """Create sample package data dictionary for scoring."""
    return {
        "name": "requests",
        "package_name": "requests",
        "registry": "pypi",
        "full_name": "psf/requests",
        "url": "https://github.com/psf/requests",
        "github_url": "https://github.com/psf/requests",
        "description": "Python HTTP for Humans",
        "license": "Apache-2.0",
        "language": "python",
        "identity_verified": True,
        "weekly_downloads": 45000000,
        "star_count": 50000,
        "fork_count": 9000,
        "fork_to_star_ratio": 0.18,
        "days_since_last_commit": 5,
        "open_issue_count": 250,
        "closed_issues_last_year": 800,
        "mttr_median_days": 7.5,
        "mttr_mad": 3.2,
        "mttr_state": "measured",
        "weekly_commit_cv": 0.25,
        "recent_committer_count": 25,
        "first_release_date": datetime(2011, 2, 13, tzinfo=timezone.utc),
        "latest_version": "2.31.0",
        "release_cv": 0.3,
        "major_versions_per_year": 0.15,
        "direct_dep_count": 5,
        "vulnerable_dep_count": 0,
        "deprecated_dep_count": 0,
        "reverse_dep_count": 150000,
        "service_note": None,
    }


@pytest.fixture
def sample_config():
    """Create sample configuration for testing."""
    return {
        "weights": {
            "reliability": 0.30,
            "adoption": 0.20,
            "versioning": 0.20,
            "activity_regularity": 0.15,
            "dependency_health": 0.15,
        },
        "floor_filter": {
            "min_weekly_downloads": 350,
            "min_stars": 50,
        },
        "reliability": {
            "issues_sufficient_min_count": 10,
            "open_to_closed_ratio_threshold": 2.0,
            "last_close_stale_days_warning": 365,
            "last_close_stale_days_dormant": 540,
            "null_state_scores": {
                "issues_disabled": 0.50,
                "low_volume_healthy": 0.65,
                "low_volume_backlog": 0.25,
            },
        },
        "adoption": {
            "committer_saturation": 10,
            "revdep_saturation": 200,
            "fsr_reference": {
                "python": 0.13,
                "javascript": 0.10,
                "typescript": 0.10,
                "go": 0.12,
                "rust": 0.08,
                "default": 0.13,
            },
        },
        "versioning": {
            "recency_halflife_days": 180,
        },
        "dependency_health": {
            "dep_health_deprecated_threshold": 2,
        },
        "confidence": {
            "full_trust_age_years": 3.0,
            "neutral_prior": 0.5,
        },
        "abandonment": {
            "early_warning_days": 365,
            "dormant_days": 540,
            "open_to_closed_ratio_threshold": 2.0,
        },
        "recommendation": {
            "use_existing_min": 75,
            "evaluate_min": 50,
        },
        "github": {
            "stagger_interval_ms": 100,
            "issues_lookback_months": 12,
            "issues_max_pages": 2,
            "commits_lookback_weeks": 104,
        },
        "cache_freshness": {
            "version_release": 2,
            "weekly_downloads": 7,
            "fsr_abandonment": 30,
            "mttr_mad": 21,
            "commit_regularity": 21,
            "version_graph_stability": 30,
            "dependency_health": 7,
        },
        "retrieval": {
            "max_candidates": 20,
        },
        "ingestion": {
            "char_budget": 24000,
            "ingest_max_repo_mb": 100,
            "ingest_timeout_seconds": 30,
        },
    }
