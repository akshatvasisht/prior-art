"""Tests for find_alternatives orchestration pipeline."""

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from priorart.core.find_alternatives import (
    _collect_package_signals,
    _fetch_fresh_signals,
    _save_to_cache,
    find_alternatives,
)
from priorart.core.github_client import GitHubSignals
from priorart.core.registry import PackageCandidate
from priorart.core.utils import load_config


@pytest.fixture
def mock_config(sample_config):
    """Config dict with all required keys."""
    return sample_config


@pytest.fixture
def mock_candidate():
    return PackageCandidate(
        name="requests",
        registry="pypi",
        description="HTTP for Humans",
        version="2.31.0",
        github_url="https://github.com/psf/requests",
        maintainers=["kennethreitz"],
        weekly_downloads=45_000_000,
    )


# --- load_config ---


def test_load_config_returns_dict():
    """Config loads and validates weight sum."""
    config = load_config()
    assert isinstance(config, dict)
    assert abs(sum(config["weights"].values()) - 1.0) < 0.01


def test_load_config_rejects_bad_weights():
    """Config validation catches invalid weight sums."""
    bad_yaml = "weights:\n  reliability: 0.99\n  adoption: 0.01\n  versioning: 0.0\n  activity_regularity: 0.0\n  dependency_health: 0.0\n"
    with patch("priorart.core.utils.files") as mock_files:
        mock_files.return_value.joinpath.return_value.read_text.return_value = bad_yaml
        # Sum is 1.0, so it should pass — test with truly bad weights
    bad_yaml2 = "weights:\n  reliability: 0.50\n  adoption: 0.50\n  versioning: 0.50\n  activity_regularity: 0.0\n  dependency_health: 0.0\n"
    with patch("priorart.core.utils.files") as mock_files:
        mock_files.return_value.joinpath.return_value.read_text.return_value = bad_yaml2
        with pytest.raises(ValueError, match="must sum to 1.0"):
            load_config()


# --- find_alternatives full pipeline ---


@patch("priorart.core.find_alternatives.load_config")
@patch("priorart.core.find_alternatives.SQLiteCache")
@patch("priorart.core.find_alternatives.QueryMapper")
@patch("priorart.core.find_alternatives.get_registry_client")
@patch("priorart.core.find_alternatives._collect_package_signals")
def test_find_alternatives_success(
    mock_collect,
    mock_get_client,
    mock_mapper_cls,
    mock_cache_cls,
    mock_load_config,
    mock_config,
    sample_package_data,
):
    """Successful pipeline returns scored packages."""
    mock_load_config.return_value = mock_config

    # QueryMapper returns a match
    mapper_instance = MagicMock()
    from priorart.core.query import QueryResult

    mapper_instance.map_query.return_value = QueryResult(
        matched=True,
        search_query="python http client",
        confidence=0.9,
        service_note=None,
    )
    mock_mapper_cls.return_value = mapper_instance

    # Registry returns candidates
    client_ctx = MagicMock()
    client_instance = MagicMock()
    client_instance.search.return_value = [
        PackageCandidate(
            name="requests",
            registry="pypi",
            description="HTTP",
            version="2.31.0",
            github_url="https://github.com/psf/requests",
            weekly_downloads=45_000_000,
        ),
    ]
    client_ctx.__enter__ = MagicMock(return_value=client_instance)
    client_ctx.__exit__ = MagicMock(return_value=False)
    mock_get_client.return_value = client_ctx

    # Signal collection returns package data
    mock_collect.return_value = sample_package_data

    result = find_alternatives("python", "http client")

    assert result["status"] == "success"
    assert result["count"] >= 1
    assert "packages" in result
    assert result["packages"][0]["name"] == "requests"
    assert "health_score" in result["packages"][0]
    assert "recommendation" in result["packages"][0]


@patch("priorart.core.find_alternatives.load_config")
@patch("priorart.core.find_alternatives.SQLiteCache")
@patch("priorart.core.find_alternatives.QueryMapper")
def test_find_alternatives_no_taxonomy_match(
    mock_mapper_cls, mock_cache_cls, mock_load_config, mock_config
):
    """Returns no_match when taxonomy doesn't match the task."""
    mock_load_config.return_value = mock_config

    mapper_instance = MagicMock()
    from priorart.core.query import QueryResult

    mapper_instance.map_query.return_value = QueryResult(matched=False)
    mapper_instance.get_no_match_response.return_value = {
        "status": "no_match",
        "message": "Could not map task to a known category",
    }
    mock_mapper_cls.return_value = mapper_instance

    result = find_alternatives("python", "quantum flux capacitor")

    assert result["status"] == "no_match"


@patch("priorart.core.find_alternatives.load_config")
@patch("priorart.core.find_alternatives.SQLiteCache")
@patch("priorart.core.find_alternatives.QueryMapper")
@patch("priorart.core.find_alternatives.get_registry_client")
def test_find_alternatives_no_registry_results(
    mock_get_client, mock_mapper_cls, mock_cache_cls, mock_load_config, mock_config
):
    """Returns no_results when registry search finds nothing."""
    mock_load_config.return_value = mock_config

    mapper_instance = MagicMock()
    from priorart.core.query import QueryResult

    mapper_instance.map_query.return_value = QueryResult(
        matched=True, search_query="obscure query", confidence=0.8
    )
    mock_mapper_cls.return_value = mapper_instance

    client_ctx = MagicMock()
    client_instance = MagicMock()
    client_instance.search.return_value = []
    client_ctx.__enter__ = MagicMock(return_value=client_instance)
    client_ctx.__exit__ = MagicMock(return_value=False)
    mock_get_client.return_value = client_ctx

    result = find_alternatives("python", "obscure thing")

    assert result["status"] == "no_results"


@patch("priorart.core.find_alternatives.load_config")
@patch("priorart.core.find_alternatives.SQLiteCache")
@patch("priorart.core.find_alternatives.QueryMapper")
@patch("priorart.core.find_alternatives.get_registry_client")
@patch("priorart.core.find_alternatives._collect_package_signals")
def test_find_alternatives_below_threshold(
    mock_collect, mock_get_client, mock_mapper_cls, mock_cache_cls, mock_load_config, mock_config
):
    """Returns below_threshold when all candidates fail floor filter."""
    mock_load_config.return_value = mock_config

    mapper_instance = MagicMock()
    from priorart.core.query import QueryResult

    mapper_instance.map_query.return_value = QueryResult(
        matched=True, search_query="test", confidence=0.8
    )
    mock_mapper_cls.return_value = mapper_instance

    client_ctx = MagicMock()
    client_instance = MagicMock()
    client_instance.search.return_value = [
        PackageCandidate(
            name="tiny-pkg",
            registry="pypi",
            description="tiny",
            version="0.1.0",
            weekly_downloads=10,
        ),
    ]
    client_ctx.__enter__ = MagicMock(return_value=client_instance)
    client_ctx.__exit__ = MagicMock(return_value=False)
    mock_get_client.return_value = client_ctx

    # Return data with very low downloads and stars
    mock_collect.return_value = {
        "name": "tiny-pkg",
        "package_name": "tiny-pkg",
        "registry": "pypi",
        "weekly_downloads": 10,
        "star_count": 5,
        "language": "python",
    }

    result = find_alternatives("python", "test")

    assert result["status"] == "below_threshold"


@patch("priorart.core.find_alternatives.load_config")
def test_find_alternatives_config_error(mock_load_config):
    """Returns error when config loading fails."""
    mock_load_config.side_effect = ValueError("bad config")

    result = find_alternatives("python", "http client")

    assert result["status"] == "error"


# --- _collect_package_signals ---


@patch("priorart.core.find_alternatives._fetch_fresh_signals")
@patch("priorart.core.find_alternatives._save_to_cache")
def test_collect_signals_fresh(mock_save, mock_fetch, mock_candidate, mock_config):
    """Cold cache path: fetches fresh signals and saves to cache."""
    cache = MagicMock()
    cache.get.return_value = None  # Cache miss

    mock_fetch.return_value = {
        "star_count": 50000,
        "fork_count": 9000,
        "weekly_downloads": 45_000_000,
    }

    result = _collect_package_signals(mock_candidate, "python", cache, mock_config, None)

    assert result is not None
    assert result["star_count"] == 50000
    assert result["github_url"] == "https://github.com/psf/requests"
    mock_save.assert_called_once()


def test_collect_signals_cached(mock_candidate, mock_config, sample_package_snapshot):
    """Warm cache path: uses cached snapshot instead of fetching."""
    cache = MagicMock()
    cache.get.return_value = sample_package_snapshot

    with patch("priorart.core.find_alternatives._fetch_fresh_signals") as mock_fetch:
        result = _collect_package_signals(mock_candidate, "python", cache, mock_config, None)

    mock_fetch.assert_not_called()
    assert result is not None
    assert result["star_count"] == 50000


def test_collect_signals_no_github_url(mock_config):
    """Returns None when no GitHub URL can be resolved."""
    candidate = PackageCandidate(
        name="obscure",
        registry="pypi",
        description="no github",
        version="1.0.0",
        github_url=None,
    )
    cache = MagicMock()
    cache.get.return_value = None

    with patch("priorart.core.find_alternatives.DepsDevClient") as mock_deps:
        ctx = MagicMock()
        deps_instance = MagicMock()
        deps_instance.get_identity_fallback.return_value = None
        ctx.__enter__ = MagicMock(return_value=deps_instance)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_deps.return_value = ctx

        result = _collect_package_signals(candidate, "python", cache, mock_config, None)

    assert result is None


# --- _save_to_cache ---


def test_save_to_cache(mock_candidate):
    """Saves a SignalSnapshot to cache with refresh timestamps."""
    cache = MagicMock()
    package_data = {
        "github_url": "https://github.com/psf/requests",
        "identity_verified": True,
        "star_count": 50000,
        "weekly_downloads": 45_000_000,
    }

    _save_to_cache(mock_candidate, package_data, cache)

    cache.set.assert_called_once()
    snapshot = cache.set.call_args[0][0]
    assert snapshot.package_name == "requests"
    assert snapshot.star_count == 50000
    assert snapshot.downloads_refreshed_at is not None


# --- explain mode ---


@patch("priorart.core.find_alternatives.load_config")
@patch("priorart.core.find_alternatives.SQLiteCache")
@patch("priorart.core.find_alternatives.QueryMapper")
@patch("priorart.core.find_alternatives.get_registry_client")
@patch("priorart.core.find_alternatives._collect_package_signals")
def test_find_alternatives_explain_mode(
    mock_collect,
    mock_get_client,
    mock_mapper_cls,
    mock_cache_cls,
    mock_load_config,
    mock_config,
    sample_package_data,
):
    """Explain mode includes score_breakdown in output."""
    mock_load_config.return_value = mock_config

    mapper_instance = MagicMock()
    from priorart.core.query import QueryResult

    mapper_instance.map_query.return_value = QueryResult(
        matched=True, search_query="python http client", confidence=0.9
    )
    mock_mapper_cls.return_value = mapper_instance

    client_ctx = MagicMock()
    client_instance = MagicMock()
    client_instance.search.return_value = [
        PackageCandidate(
            name="requests",
            registry="pypi",
            description="HTTP",
            version="2.31.0",
            github_url="https://github.com/psf/requests",
            weekly_downloads=45_000_000,
        ),
    ]
    client_ctx.__enter__ = MagicMock(return_value=client_instance)
    client_ctx.__exit__ = MagicMock(return_value=False)
    mock_get_client.return_value = client_ctx

    mock_collect.return_value = sample_package_data

    result = find_alternatives("python", "http client", explain=True)

    assert result["status"] == "success"
    pkg = result["packages"][0]
    assert "score_breakdown" in pkg
    bd = pkg["score_breakdown"]
    assert all(
        k in bd
        for k in (
            "reliability",
            "adoption",
            "versioning",
            "activity_regularity",
            "dependency_health",
        )
    )


# --- _collect_package_signals identity verification ---


@patch("priorart.core.find_alternatives._fetch_fresh_signals")
@patch("priorart.core.find_alternatives._save_to_cache")
def test_collect_signals_identity_fails(mock_save, mock_fetch, mock_candidate, mock_config):
    """Identity verification failure flags the package."""
    cache = MagicMock()
    cache.get.return_value = None

    mock_fetch.return_value = {"star_count": 5000, "fork_count": 500}

    with patch.dict(os.environ, {"GITHUB_TOKEN": "test_token"}):
        with patch("priorart.core.find_alternatives.GitHubClient") as mock_gh_cls:
            gh_instance = MagicMock()
            gh_instance.verify_identity.return_value = False
            mock_gh_cls.return_value = gh_instance

            result = _collect_package_signals(mock_candidate, "python", cache, mock_config, None)

    assert result is not None
    assert result["identity_verified"] is False


# --- _fetch_fresh_signals ---


def test_fetch_fresh_signals_with_github(mock_candidate, mock_config):
    """With GITHUB_TOKEN set, fetches both GitHub and deps.dev signals."""
    gh_signals = GitHubSignals(
        star_count=50000,
        fork_count=9000,
        open_issues_count=250,
        mttr_median_days=3.5,
        mttr_mad=1.2,
        mttr_state="measured",
        weekly_commit_cv=0.25,
        recent_committer_count=15,
        days_since_last_commit=5,
        closed_issues_last_year=800,
    )

    with patch.dict(os.environ, {"GITHUB_TOKEN": "test_token"}):
        with patch("priorart.core.find_alternatives.GitHubClient") as mock_gh_cls:
            gh_instance = MagicMock()
            gh_instance.parse_github_url.return_value = ("psf", "requests")
            gh_instance.get_repository_signals.return_value = gh_signals
            mock_gh_cls.return_value = gh_instance

            with patch("priorart.core.find_alternatives.DepsDevClient") as mock_deps_cls:
                deps_instance = MagicMock()
                deps_data = MagicMock()
                deps_data.first_release_date = datetime(2012, 1, 1, tzinfo=timezone.utc)
                deps_data.latest_version = "2.31.0"
                deps_data.release_cv = 0.3
                deps_data.major_versions_per_year = 0.15
                deps_data.reverse_dep_count = 150000
                deps_data.dependency_info = MagicMock()
                deps_data.dependency_info.direct_count = 5
                deps_data.dependency_info.vulnerable_count = 0
                deps_data.dependency_info.deprecated_count = 0
                deps_instance.get_package_data.return_value = deps_data
                ctx = MagicMock()
                ctx.__enter__ = MagicMock(return_value=deps_instance)
                ctx.__exit__ = MagicMock(return_value=False)
                mock_deps_cls.return_value = ctx

                signals = _fetch_fresh_signals(
                    mock_candidate, "https://github.com/psf/requests", mock_config
                )

    assert signals["star_count"] == 50000
    assert signals["mttr_state"] == "measured"
    assert signals["first_release_date"] == datetime(2012, 1, 1, tzinfo=timezone.utc)
    assert signals["reverse_dep_count"] == 150000


def test_fetch_fresh_signals_no_github_token(mock_candidate, mock_config):
    """Without GITHUB_TOKEN, only deps.dev signals are collected."""
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("GITHUB_TOKEN", None)

        with patch("priorart.core.find_alternatives.DepsDevClient") as mock_deps_cls:
            deps_instance = MagicMock()
            deps_data = MagicMock()
            deps_data.first_release_date = datetime(2015, 1, 1, tzinfo=timezone.utc)
            deps_data.latest_version = "1.0.0"
            deps_data.release_cv = 0.5
            deps_data.major_versions_per_year = 0.5
            deps_data.reverse_dep_count = 100
            deps_data.dependency_info = None
            deps_instance.get_package_data.return_value = deps_data
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=deps_instance)
            ctx.__exit__ = MagicMock(return_value=False)
            mock_deps_cls.return_value = ctx

            signals = _fetch_fresh_signals(
                mock_candidate, "https://github.com/psf/requests", mock_config
            )

    # Should have deps.dev data but no GitHub data
    assert signals.get("first_release_date") == datetime(2015, 1, 1, tzinfo=timezone.utc)
    assert "star_count" not in signals
    assert "mttr_state" not in signals


# --- Edge cases in _collect_package_signals ---


def test_collect_signals_github_url_from_snapshot(mock_config):
    """GitHub URL is retrieved from cached snapshot when candidate has none."""
    candidate = PackageCandidate(
        name="cached-pkg",
        registry="pypi",
        description="Has cache",
        version="1.0.0",
        github_url=None,
    )
    cache = MagicMock()

    snapshot = MagicMock()
    snapshot.identity_verified = True
    snapshot.github_url = "https://github.com/owner/cached-pkg"
    snapshot.weekly_downloads = 1000
    snapshot.star_count = 500
    snapshot.fork_count = 50
    snapshot.fork_to_star_ratio = 0.1
    snapshot.days_since_last_commit = 5
    snapshot.open_issue_count = 10
    snapshot.closed_issues_last_year = 50
    snapshot.mttr_median_days = 3.0
    snapshot.mttr_mad = 1.0
    snapshot.mttr_state = "measured"
    snapshot.weekly_commit_cv = 0.3
    snapshot.recent_committer_count = 5
    snapshot.first_release_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
    snapshot.latest_version = "1.0.0"
    snapshot.release_cv = 0.3
    snapshot.major_versions_per_year = 0.5
    snapshot.direct_dep_count = 3
    snapshot.vulnerable_dep_count = 0
    snapshot.deprecated_dep_count = 0
    snapshot.reverse_dep_count = 100
    cache.get.return_value = snapshot

    result = _collect_package_signals(candidate, "python", cache, mock_config, None)

    assert result is not None
    assert result["github_url"] == "https://github.com/owner/cached-pkg"


@patch("priorart.core.find_alternatives._fetch_fresh_signals")
@patch("priorart.core.find_alternatives._save_to_cache")
def test_collect_signals_identity_verification_exception(
    mock_save, mock_fetch, mock_candidate, mock_config
):
    """Exception during identity verification flags identity as unverified."""
    cache = MagicMock()
    cache.get.return_value = None
    mock_fetch.return_value = {"star_count": 5000}

    with patch.dict(os.environ, {"GITHUB_TOKEN": "test_token"}):
        with patch("priorart.core.find_alternatives.GitHubClient") as mock_gh_cls:
            mock_gh_cls.side_effect = RuntimeError("init failed")

            result = _collect_package_signals(mock_candidate, "python", cache, mock_config, None)

    assert result is not None
    assert result["identity_verified"] is False


@patch("priorart.core.find_alternatives._fetch_fresh_signals")
def test_collect_signals_cache_save_exception(mock_fetch, mock_candidate, mock_config):
    """Cache save failure doesn't prevent returning data."""
    cache = MagicMock()
    cache.get.return_value = None
    cache.set.side_effect = RuntimeError("disk full")
    mock_fetch.return_value = {"star_count": 5000}

    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("GITHUB_TOKEN", None)
        result = _collect_package_signals(mock_candidate, "python", cache, mock_config, None)

    assert result is not None


# --- Error paths in find_alternatives ---


@patch("priorart.core.find_alternatives.load_config")
@patch("priorart.core.find_alternatives.SQLiteCache")
@patch("priorart.core.find_alternatives.QueryMapper")
@patch("priorart.core.find_alternatives.get_registry_client")
@patch("priorart.core.find_alternatives._collect_package_signals")
def test_find_alternatives_collect_signals_exception(
    mock_collect, mock_get_client, mock_mapper_cls, mock_cache_cls, mock_load_config, mock_config
):
    """Exception during signal collection is caught and skipped."""
    mock_load_config.return_value = mock_config

    mapper_instance = MagicMock()
    from priorart.core.query import QueryResult

    mapper_instance.map_query.return_value = QueryResult(
        matched=True, search_query="test", confidence=0.8
    )
    mock_mapper_cls.return_value = mapper_instance

    client_ctx = MagicMock()
    client_instance = MagicMock()
    client_instance.search.return_value = [
        PackageCandidate(
            name="pkg",
            registry="pypi",
            version="1.0.0",
            github_url="https://github.com/o/r",
            weekly_downloads=1000,
        ),
    ]
    client_ctx.__enter__ = MagicMock(return_value=client_instance)
    client_ctx.__exit__ = MagicMock(return_value=False)
    mock_get_client.return_value = client_ctx

    # Signal collection raises
    mock_collect.side_effect = RuntimeError("API error")

    result = find_alternatives("python", "test")

    # All candidates failed → no_results
    assert result["status"] == "no_results"


@patch("priorart.core.find_alternatives.load_config")
@patch("priorart.core.find_alternatives.SQLiteCache")
@patch("priorart.core.find_alternatives.QueryMapper")
@patch("priorart.core.find_alternatives.get_registry_client")
@patch("priorart.core.find_alternatives._collect_package_signals")
def test_find_alternatives_score_exception(
    mock_collect,
    mock_get_client,
    mock_mapper_cls,
    mock_cache_cls,
    mock_load_config,
    mock_config,
    sample_package_data,
):
    """Exception during scoring is caught and skipped."""
    mock_load_config.return_value = mock_config

    mapper_instance = MagicMock()
    from priorart.core.query import QueryResult

    mapper_instance.map_query.return_value = QueryResult(
        matched=True, search_query="test", confidence=0.8
    )
    mock_mapper_cls.return_value = mapper_instance

    client_ctx = MagicMock()
    client_instance = MagicMock()
    client_instance.search.return_value = [
        PackageCandidate(
            name="requests",
            registry="pypi",
            version="2.31.0",
            github_url="https://github.com/psf/requests",
            weekly_downloads=45_000_000,
        ),
    ]
    client_ctx.__enter__ = MagicMock(return_value=client_instance)
    client_ctx.__exit__ = MagicMock(return_value=False)
    mock_get_client.return_value = client_ctx

    mock_collect.return_value = sample_package_data

    # Make scorer.score_package raise
    with patch("priorart.core.find_alternatives.PackageScorer") as mock_scorer_cls:
        scorer_instance = MagicMock()
        scorer_instance.apply_floor_filter.return_value = [sample_package_data]
        scorer_instance.score_package.side_effect = RuntimeError("scoring failed")
        mock_scorer_cls.return_value = scorer_instance

        result = find_alternatives("python", "test")

    # All scoring failed → empty results list
    assert result["status"] == "success"
    assert result["count"] == 0


# --- _fetch_fresh_signals error handling ---


def test_fetch_fresh_signals_github_api_error(mock_candidate, mock_config):
    """GitHub API error in _fetch_fresh_signals is caught gracefully."""
    with patch.dict(os.environ, {"GITHUB_TOKEN": "test_token"}):
        with patch("priorart.core.find_alternatives.GitHubClient") as mock_gh_cls:
            mock_gh_cls.side_effect = RuntimeError("GitHub init failed")

            with patch("priorart.core.find_alternatives.DepsDevClient") as mock_deps_cls:
                deps_instance = MagicMock()
                deps_instance.get_package_data.return_value = None
                ctx = MagicMock()
                ctx.__enter__ = MagicMock(return_value=deps_instance)
                ctx.__exit__ = MagicMock(return_value=False)
                mock_deps_cls.return_value = ctx

                signals = _fetch_fresh_signals(
                    mock_candidate, "https://github.com/psf/requests", mock_config
                )

    # Should still return (empty signals from GitHub, no deps.dev data either)
    assert isinstance(signals, dict)


def test_fetch_fresh_signals_deps_dev_error(mock_candidate, mock_config):
    """deps.dev API error in _fetch_fresh_signals is caught gracefully."""
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("GITHUB_TOKEN", None)

        with patch("priorart.core.find_alternatives.DepsDevClient") as mock_deps_cls:
            deps_instance = MagicMock()
            deps_instance.get_package_data.side_effect = RuntimeError("deps.dev down")
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=deps_instance)
            ctx.__exit__ = MagicMock(return_value=False)
            mock_deps_cls.return_value = ctx

            signals = _fetch_fresh_signals(
                mock_candidate, "https://github.com/psf/requests", mock_config
            )

    assert isinstance(signals, dict)


def test_collect_signals_deps_dev_fallback_exception(mock_config):
    """Exception from DepsDevClient during GitHub URL fallback is caught."""
    candidate = PackageCandidate(
        name="obscure",
        registry="pypi",
        description="no github",
        version="1.0.0",
        github_url=None,
    )
    cache = MagicMock()
    cache.get.return_value = None

    with patch("priorart.core.find_alternatives.DepsDevClient") as mock_deps:
        mock_deps.side_effect = RuntimeError("init failed")

        result = _collect_package_signals(candidate, "python", cache, mock_config, None)

    assert result is None
