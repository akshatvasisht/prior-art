"""
Main find_alternatives tool - orchestrates package discovery and scoring.

Complete pipeline:
1. Taxonomy mapping
2. Registry search
3. Identity verification
4. Floor filter
5. Signal collection (with cache)
6. Scoring
7. Top 5 results
"""

import os
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from importlib.resources import files

import yaml

from .query import QueryMapper
from .registry import get_registry_client, PackageCandidate
from .cache import SQLiteCache, SignalSnapshot
from .deps_dev import DepsDevClient
from .github_client import GitHubClient
from .scoring import PackageScorer

logger = logging.getLogger(__name__)


def load_config() -> Dict[str, Any]:
    """Load configuration from bundled config.yaml.

    Returns:
        Configuration dictionary

    Raises:
        Exception: If config file is missing or invalid.
    """
    try:
        config_text = files('priorart.data').joinpath('config.yaml').read_text()
        config = yaml.safe_load(config_text)

        # Validate weights
        weight_sum = sum(config['weights'].values())
        if abs(weight_sum - 1.0) > 0.01:
            raise ValueError(f"Scoring weights must sum to 1.0, got {weight_sum}")

        return config

    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        raise


def find_alternatives(
    language: str,
    task_description: str,
    explain: bool = False
) -> Dict[str, Any]:
    """Find alternative packages for a given task.

    This tool helps AI agents make build-vs-borrow decisions by discovering
    and scoring existing open source packages.

    DO NOT call this tool when:
    - The user has explicitly named a specific library they want to use
    - The task is project-specific functionality (not general-purpose)
    - The task is already using a well-known package

    DO call this tool when:
    - Implementing general-purpose capabilities (http clients, parsers, etc.)
    - Evaluating whether to build custom or use existing solutions
    - Discovering packages in unfamiliar ecosystems

    Args:
        language: Programming language (python, javascript, typescript, go, rust)
        task_description: Natural language description of the capability needed
        explain: Whether to include detailed scoring breakdown

    Returns:
        Dictionary with results or error message
    """
    try:
        # Load configuration
        config = load_config()

        # Initialize components
        cache = SQLiteCache()
        query_mapper = QueryMapper(confidence_threshold=config['taxonomy']['confidence_threshold'])
        scorer = PackageScorer(config)

        # Step 1: Map task description to search query
        query_result = query_mapper.map_query(task_description, language)

        if not query_result.matched:
            return query_mapper.get_no_match_response()

        # Step 2: Search registry
        registry_client = get_registry_client(language)
        max_results = config['taxonomy']['max_search_results']

        candidates = registry_client.search(query_result.search_query, max_results)

        if not candidates:
            return {
                "status": "no_results",
                "message": f"No packages found matching '{query_result.search_query}'",
                "service_note": query_result.service_note
            }

        # Step 3: Collect detailed package data
        package_data_list = []

        for candidate in candidates:
            try:
                package_data = _collect_package_signals(
                    candidate,
                    language,
                    cache,
                    config,
                    query_result.service_note
                )

                if package_data:
                    package_data_list.append(package_data)

            except Exception as e:
                logger.warning(f"Failed to collect signals for {candidate.name}: {e}")
                continue

        if not package_data_list:
            return {
                "status": "no_results",
                "message": "No packages met the minimum quality thresholds",
                "service_note": query_result.service_note
            }

        # Step 4: Apply floor filter
        filtered = scorer.apply_floor_filter(package_data_list)

        if not filtered:
            return {
                "status": "below_threshold",
                "message": "All candidates were below minimum download/star thresholds",
                "service_note": query_result.service_note
            }

        # Step 5: Score packages
        scored_packages = []
        for pkg_data in filtered:
            try:
                scored_pkg = scorer.score_package(pkg_data, explain=explain)
                scored_packages.append(scored_pkg)
            except Exception as e:
                logger.warning(f"Failed to score package {pkg_data.get('name')}: {e}")
                continue

        # Step 6: Sort by health score and return top 5
        scored_packages.sort(key=lambda p: p.health_score, reverse=True)
        top_packages = scored_packages[:5]

        # Format output
        results = []
        for pkg in top_packages:
            result = {
                "name": pkg.name,
                "full_name": pkg.full_name,
                "url": pkg.url,
                "package_name": pkg.package_name,
                "registry": pkg.registry,
                "description": pkg.description,
                "health_score": pkg.health_score,
                "recommendation": pkg.recommendation,
                "identity_verified": pkg.identity_verified,
                "age_years": round(pkg.age_years, 1),
                "weekly_downloads": pkg.weekly_downloads,
                "license": pkg.license,
                "license_warning": pkg.license_warning,
                "dep_health_flag": pkg.dep_health_flag,
                "likely_abandoned": pkg.likely_abandoned,
            }

            if explain and pkg.score_breakdown:
                result["score_breakdown"] = {
                    "reliability": pkg.score_breakdown.reliability,
                    "adoption": pkg.score_breakdown.adoption,
                    "versioning": pkg.score_breakdown.versioning,
                    "activity_regularity": pkg.score_breakdown.activity_regularity,
                    "dependency_health": pkg.score_breakdown.dependency_health,
                }

                if pkg.score_breakdown.reliability_details:
                    result["reliability_details"] = pkg.score_breakdown.reliability_details
                if pkg.score_breakdown.adoption_details:
                    result["adoption_details"] = pkg.score_breakdown.adoption_details

            results.append(result)

        return {
            "status": "success",
            "count": len(results),
            "packages": results,
            "service_note": query_result.service_note
        }

    except Exception as e:
        logger.error(f"Error in find_alternatives: {e}", exc_info=True)
        return {
            "status": "error",
            "message": str(e)
        }


def _collect_package_signals(
    candidate: PackageCandidate,
    language: str,
    cache: SQLiteCache,
    config: Dict[str, Any],
    service_note: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Collect all signals for a package, using cache when fresh.

    Args:
        candidate: package candidate from registry
        language: programming language
        cache: cache backend
        config: system configuration
        service_note: optional note from query mapper

    Returns:
        Dictionary of signals or None if GitHub URL not found
    """

    package_data = {
        "name": candidate.name,
        "package_name": candidate.name,
        "registry": candidate.registry,
        "description": candidate.description,
        "license": candidate.license,
        "language": language,
        "service_note": service_note,
    }

    # Check cache
    snapshot = cache.get(candidate.name, candidate.registry)

    # If no cache or identity not verified, we need GitHub data
    needs_github = snapshot is None or not snapshot.identity_verified

    # Get or verify GitHub URL
    github_url = candidate.github_url

    if not github_url and snapshot:
        github_url = snapshot.github_url

    if not github_url:
        # Try deps.dev fallback
        try:
            deps_client = DepsDevClient()
            github_url = deps_client.get_identity_fallback(candidate.name, candidate.registry)
        except:
            pass

    if not github_url:
        logger.warning(f"No GitHub URL found for {candidate.name}")
        return None

    package_data["github_url"] = github_url
    package_data["url"] = github_url

    # Parse GitHub URL for full_name
    import re
    match = re.match(r'https://github\.com/([^/]+)/([^/]+)', github_url)
    if match:
        owner, repo = match.groups()
        package_data["full_name"] = f"{owner}/{repo}"
    else:
        package_data["full_name"] = candidate.name

    # Verify identity if needed
    identity_verified = True
    if needs_github:
        try:
            github_token = os.getenv('GITHUB_TOKEN')
            if github_token:
                github_client = GitHubClient(
                    token=github_token,
                    stagger_ms=config['github']['stagger_interval_ms']
                )

                identity_verified = github_client.verify_identity(
                    github_url,
                    candidate.name,
                    candidate.maintainers or []
                )

                if not identity_verified:
                    logger.warning(f"Identity verification failed for {candidate.name}")
                    # Still include but flag it
                    package_data["identity_verified"] = False
        except Exception as e:
            logger.warning(f"Identity verification error for {candidate.name}: {e}")
            identity_verified = False

    package_data["identity_verified"] = identity_verified

    # Collect signals from cache or fresh API calls
    if snapshot:
        # Use cached data
        package_data.update({
            "weekly_downloads": snapshot.weekly_downloads,
            "download_percentile": snapshot.download_percentile,
            "star_count": snapshot.star_count,
            "fork_count": snapshot.fork_count,
            "fork_to_star_ratio": snapshot.fork_to_star_ratio,
            "days_since_last_commit": snapshot.days_since_last_commit,
            "open_issue_count": snapshot.open_issue_count,
            "closed_issues_last_year": snapshot.closed_issues_last_year,
            "mttr_median_days": snapshot.mttr_median_days,
            "mttr_mad": snapshot.mttr_mad,
            "mttr_state": snapshot.mttr_state or "unknown",
            "weekly_commit_cv": snapshot.weekly_commit_cv,
            "recent_committer_count": snapshot.recent_committer_count,
            "first_release_date": snapshot.first_release_date,
            "latest_version": snapshot.latest_version,
            "release_cv": snapshot.release_cv,
            "major_versions_per_year": snapshot.major_versions_per_year,
            "direct_dep_count": snapshot.direct_dep_count,
            "vulnerable_dep_count": snapshot.vulnerable_dep_count,
            "deprecated_dep_count": snapshot.deprecated_dep_count,
            "reverse_dep_count": snapshot.reverse_dep_count,
        })
    else:
        # Fetch fresh data (cold cache)
        package_data.update(_fetch_fresh_signals(candidate, github_url, config))

        # Save to cache
        try:
            _save_to_cache(candidate, package_data, cache)
        except Exception as e:
            logger.warning(f"Failed to save cache for {candidate.name}: {e}")

    return package_data


def _fetch_fresh_signals(
    candidate: PackageCandidate,
    github_url: str,
    config: Dict[str, Any]
) -> Dict[str, Any]:
    """Fetch fresh signals from APIs.

    Args:
        candidate: package candidate from registry
        github_url: verified GitHub URL
        config: system configuration

    Returns:
        Dictionary of collected signals
    """
    signals = {}

    # Get GitHub signals
    github_token = os.getenv('GITHUB_TOKEN')
    if github_token:
        try:
            github_client = GitHubClient(
                token=github_token,
                stagger_ms=config['github']['stagger_interval_ms']
            )

            parsed = github_client.parse_github_url(github_url)
            if parsed:
                owner, repo = parsed
                gh_signals = github_client.get_repository_signals(
                    owner, repo,
                    issues_lookback_months=config['github']['issues_lookback_months'],
                    commits_lookback_weeks=config['github']['commits_lookback_weeks'],
                    issues_max_pages=config['github']['issues_max_pages']
                )

                if gh_signals:
                    signals.update({
                        "star_count": gh_signals.star_count,
                        "fork_count": gh_signals.fork_count,
                        "fork_to_star_ratio": gh_signals.fork_to_star_ratio if gh_signals.fork_count and gh_signals.star_count else 0,
                        "days_since_last_commit": gh_signals.days_since_last_commit,
                        "open_issue_count": gh_signals.open_issues_count,
                        "closed_issues_last_year": gh_signals.closed_issues_last_year,
                        "mttr_median_days": gh_signals.mttr_median_days,
                        "mttr_mad": gh_signals.mttr_mad,
                        "mttr_state": gh_signals.mttr_state,
                        "weekly_commit_cv": gh_signals.weekly_commit_cv,
                        "recent_committer_count": gh_signals.recent_committer_count,
                    })

                    if gh_signals.fork_count and gh_signals.star_count:
                        signals["fork_to_star_ratio"] = gh_signals.fork_count / gh_signals.star_count

        except Exception as e:
            logger.warning(f"GitHub API error: {e}")

    # Get deps.dev data
    try:
        deps_client = DepsDevClient()
        deps_data = deps_client.get_package_data(candidate.name, candidate.registry)

        if deps_data:
            signals.update({
                "first_release_date": deps_data.first_release_date,
                "latest_version": deps_data.latest_version,
                "release_cv": deps_data.release_cv,
                "major_versions_per_year": deps_data.major_versions_per_year,
                "reverse_dep_count": deps_data.reverse_dep_count,
            })

            if deps_data.dependency_info:
                signals.update({
                    "direct_dep_count": deps_data.dependency_info.direct_count,
                    "vulnerable_dep_count": deps_data.dependency_info.vulnerable_count,
                    "deprecated_dep_count": deps_data.dependency_info.deprecated_count,
                })

    except Exception as e:
        logger.warning(f"deps.dev error: {e}")

    # Add download data from candidate
    if candidate.weekly_downloads:
        signals["weekly_downloads"] = candidate.weekly_downloads

    return signals


def _save_to_cache(candidate: PackageCandidate, package_data: Dict[str, Any], cache: SQLiteCache) -> None:
    """Save package signals to cache.

    Args:
        candidate: package candidate from registry
        package_data: collected signals
        cache: cache backend
    """
    snapshot = SignalSnapshot(
        package_name=candidate.name,
        registry=candidate.registry,
        github_url=package_data.get("github_url"),
        identity_verified=package_data.get("identity_verified", True),
        weekly_downloads=package_data.get("weekly_downloads"),
        star_count=package_data.get("star_count"),
        fork_count=package_data.get("fork_count"),
        fork_to_star_ratio=package_data.get("fork_to_star_ratio"),
        days_since_last_commit=package_data.get("days_since_last_commit"),
        open_issue_count=package_data.get("open_issue_count"),
        closed_issues_last_year=package_data.get("closed_issues_last_year"),
        mttr_median_days=package_data.get("mttr_median_days"),
        mttr_mad=package_data.get("mttr_mad"),
        mttr_state=package_data.get("mttr_state"),
        weekly_commit_cv=package_data.get("weekly_commit_cv"),
        recent_committer_count=package_data.get("recent_committer_count"),
        latest_version=package_data.get("latest_version"),
        first_release_date=package_data.get("first_release_date"),
        release_cv=package_data.get("release_cv"),
        major_versions_per_year=package_data.get("major_versions_per_year"),
        direct_dep_count=package_data.get("direct_dep_count"),
        vulnerable_dep_count=package_data.get("vulnerable_dep_count"),
        deprecated_dep_count=package_data.get("deprecated_dep_count"),
        reverse_dep_count=package_data.get("reverse_dep_count"),
        description=package_data.get("description"),
        license=package_data.get("license"),
    )

    # Set refresh timestamps
    snapshot.downloads_refreshed_at = datetime.utcnow()
    snapshot.repo_refreshed_at = datetime.utcnow()
    snapshot.mttr_refreshed_at = datetime.utcnow()
    snapshot.regularity_refreshed_at = datetime.utcnow()
    snapshot.version_refreshed_at = datetime.utcnow()
    snapshot.dep_health_refreshed_at = datetime.utcnow()

    cache.set(snapshot)


