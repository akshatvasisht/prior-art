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

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from .build_cost import enrich_build_vs_borrow
from .cache import SignalSnapshot, SQLiteCache
from .deps_dev import DepsDevClient, DepsDevData
from .github_client import GitHubClient
from .registry import PackageCandidate
from .retrieval import retrieve_candidates
from .scorecard_client import ScorecardClient
from .scoring import PackageScorer, ScoredPackage
from .utils import load_config, parse_github_url

logger = logging.getLogger(__name__)


# Signal-fetch group names. Used in per-group `logger.warning` messages emitted
# when individual signal fetches fail; exposed here so tests can pin the group
# contract without hard-coding log wording. See _fetch_fresh_signals.
SIGNAL_GROUP_STARS_FORKS = "stars-and-forks"
SIGNAL_GROUP_MTTR = "MTTR"
SIGNAL_GROUP_COMMIT_REGULARITY = "commit-regularity"
SIGNAL_GROUP_VERSION_GRAPH = "version-graph"
SIGNAL_GROUP_DEP_HEALTH = "dep-health"


# Cache freshness is enforced per source, not per whole row: each signal group
# has its own window (config.cache_freshness) and belongs to one upstream
# source, so on a cache hit only the sources whose groups have expired are
# re-fetched and the rest are served from cache.
_GROUP_WINDOW_KEY = {
    "downloads": "weekly_downloads",
    "repo": "fsr_abandonment",
    "mttr": "mttr_mad",
    "regularity": "commit_regularity",
    "version": "version_graph_stability",
    "dep_health": "dependency_health",
}
_GROUP_SOURCE = {
    "repo": "github",
    "mttr": "github",
    "regularity": "github",
    "version": "deps_dev",
    "dep_health": "deps_dev",
    "downloads": "downloads",
}
# Snapshot fields owned by each group, used to refresh just that group in cache.
_GROUP_FIELDS = {
    "downloads": ["weekly_downloads"],
    "repo": [
        "star_count",
        "fork_count",
        "fork_to_star_ratio",
        "days_since_last_commit",
        "open_issue_count",
        "scorecard_overall",
        "scorecard_reliability_bucket",
        "scorecard_dep_health_bucket",
    ],
    "mttr": ["mttr_median_days", "mttr_mad", "mttr_state", "closed_issues_last_year"],
    "regularity": ["weekly_commit_cv", "recent_committer_count"],
    "version": [
        "first_release_date",
        "latest_version",
        "release_cv",
        "major_versions_per_year",
        "reverse_dep_count",
    ],
    "dep_health": ["direct_dep_count", "vulnerable_dep_count", "deprecated_dep_count"],
}
# Field whose presence means a group has real (non-defaulted) data — drives the
# data_quality provenance block returned to the caller.
_GROUP_SENTINEL = {
    "downloads": "weekly_downloads",
    "repo": "star_count",
    "mttr": "mttr_state",
    "regularity": "weekly_commit_cv",
    "version": "latest_version",
    "dep_health": "direct_dep_count",
}


# GitHub rate-limit circuit breaker (process-scoped). Once GitHub returns a
# 403/429, stop calling it until the reported reset time so a rate-limited run
# fails fast instead of staggering ~10 calls per remaining candidate.
_GITHUB_COOLDOWN_S = 60.0
_github_blocked_until = 0.0


def _github_circuit_open() -> bool:
    return time.time() < _github_blocked_until


def _maybe_trip_github_circuit(exc: Exception) -> None:
    """Open the GitHub circuit if ``exc`` is a 403/429 rate-limit, honoring the
    ``x-ratelimit-reset`` header when present."""
    global _github_blocked_until
    if getattr(exc, "status", None) not in (403, 429):
        return
    reset = None
    raw = (getattr(exc, "headers", None) or {}).get("x-ratelimit-reset")
    if raw is not None:
        try:
            reset = float(raw)
        except (TypeError, ValueError):
            reset = None
    _github_blocked_until = reset if reset else time.time() + _GITHUB_COOLDOWN_S
    logger.warning("GitHub rate limit hit; pausing GitHub signal fetches until reset")


def _reset_github_circuit() -> None:
    """Clear the GitHub circuit. Tests call this to avoid cross-test leakage."""
    global _github_blocked_until
    _github_blocked_until = 0.0


def _stale_sources(snapshot: SignalSnapshot, config: dict[str, Any]) -> set[str]:
    """Return the sources owning at least one signal group past its window."""
    windows = config.get("cache_freshness", {})
    stale: set[str] = set()
    for group, source in _GROUP_SOURCE.items():
        window = windows.get(_GROUP_WINDOW_KEY[group])
        if window is not None and snapshot.is_signal_group_stale(group, window):
            stale.add(source)
    return stale


def _persist_refreshed_groups(
    candidate: PackageCandidate, fresh: dict[str, Any], sources: set[str], cache: SQLiteCache
) -> None:
    """Write freshly re-fetched signal groups back to cache, bumping just their
    refresh timestamps so the other groups keep their own freshness clocks."""
    for group, fields in _GROUP_FIELDS.items():
        if _GROUP_SOURCE[group] not in sources:
            continue
        payload = {f: fresh[f] for f in fields if f in fresh}
        if not payload:
            continue
        try:
            cache.update_signal_group(candidate.name, candidate.registry, group, payload)
        except Exception as e:
            logger.warning("cache refresh for %s/%s failed: %s", candidate.name, group, e)


def _build_data_quality(
    package_data: dict[str, Any],
    snapshot: SignalSnapshot | None,
    refreshed_sources: set[str],
    now: datetime,
) -> dict[str, Any]:
    """Provenance block: which signal groups carry real data, which are missing,
    and the oldest timestamp behind the data so callers can judge freshness."""
    fetched: list[str] = []
    missing: list[str] = []
    ages: list[datetime] = []
    for group, sentinel in _GROUP_SENTINEL.items():
        if package_data.get(sentinel) is None:
            missing.append(group)
            continue
        fetched.append(group)
        if snapshot is not None and _GROUP_SOURCE[group] not in refreshed_sources:
            ts = getattr(snapshot, f"{group}_refreshed_at", None)
            ages.append(_as_datetime(ts) if ts else now)
        else:
            ages.append(now)
    return {
        "signals_fetched": sorted(fetched),
        "signals_missing": sorted(missing),
        "data_as_of": min(ages, default=now).isoformat(),
    }


def _as_datetime(value: Any) -> datetime:
    """Coerce a stored timestamp (datetime or ISO string) to aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def find_alternatives(
    language: str, task_description: str, explain: bool = False, lite: bool = False
) -> dict[str, Any]:
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
        scorer = PackageScorer(config)

        max_results = config["retrieval"]["max_candidates"]
        candidates = retrieve_candidates(task_description, language, max_results, lite=lite)
        service_note: str | None = None

        if not candidates:
            return {
                "status": "no_results",
                "message": f"No packages found matching '{task_description}'",
                "service_note": service_note,
            }

        # Step 3: Collect detailed package data
        package_data_list = []

        for candidate in candidates:
            try:
                package_data = _collect_package_signals(
                    candidate, language, cache, config, service_note
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
                "service_note": service_note,
            }

        # Step 4: Apply floor filter
        filtered = scorer.apply_floor_filter(package_data_list)

        if not filtered:
            return {
                "status": "below_threshold",
                "message": "All candidates were below minimum download/star thresholds",
                "service_note": service_note,
            }

        # Step 5: Score packages + enrich with build-vs-borrow lens
        scored_packages = []
        for pkg_data in filtered:
            try:
                scored_pkg = scorer.score_package(pkg_data, explain=explain)
                enrich_build_vs_borrow(scored_pkg, pkg_data)
                scored_packages.append(scored_pkg)
            except Exception as e:
                logger.warning(
                    f"Failed to score package {pkg_data.get('name')}: {e}",
                    exc_info=True,
                )
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
                "scorecard_overall": pkg.scorecard_overall,
                "build_cost_weeks": pkg.build_cost_weeks,
                "commodity_tag": pkg.commodity_tag,
                "maintenance_liability": pkg.maintenance_liability,
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
            "service_note": service_note,
        }

    except Exception as e:
        logger.error(f"Error in find_alternatives: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


def _collect_package_signals(
    candidate: PackageCandidate,
    language: str,
    cache: SQLiteCache,
    config: dict[str, Any],
    service_note: str | None,
) -> dict[str, Any] | None:
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
            with DepsDevClient() as deps_client:
                github_url = deps_client.get_identity_fallback(candidate.name, candidate.registry)
        except Exception as e:
            # Best-effort fallback; surface at debug so operators can trace the failure.
            logger.debug("DepsDevClient identity lookup failed for %s: %s", candidate.name, e)

    if not github_url:
        logger.warning(f"No GitHub URL found for {candidate.name}")
        return None

    package_data["github_url"] = github_url
    package_data["url"] = github_url

    # github_url is already normalized to https://github.com/owner/repo
    parts = github_url.rstrip("/").split("/")
    package_data["full_name"] = f"{parts[-2]}/{parts[-1]}" if len(parts) >= 5 else candidate.name

    # Verify identity if needed
    identity_verified = True
    if needs_github:
        try:
            github_token = os.getenv("GITHUB_TOKEN")
            if github_token:
                github_client = GitHubClient(
                    token=github_token, stagger_ms=config["github"]["stagger_interval_ms"]
                )

                identity_verified = github_client.verify_identity(
                    github_url, candidate.name, candidate.maintainers or []
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
    now = datetime.now(timezone.utc)
    refreshed_sources: set[str] = set()
    if snapshot:
        # Use cached data
        package_data.update(
            {
                "weekly_downloads": snapshot.weekly_downloads,
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
                "scorecard_overall": snapshot.scorecard_overall,
                "scorecard_reliability_bucket": snapshot.scorecard_reliability_bucket,
                "scorecard_dep_health_bucket": snapshot.scorecard_dep_health_bucket,
            }
        )
        # Per-source freshness: refetch only the sources whose groups expired.
        refreshed_sources = _stale_sources(snapshot, config)
        if refreshed_sources:
            fresh = _fetch_fresh_signals(candidate, github_url, config, sources=refreshed_sources)
            package_data.update(fresh)
            _persist_refreshed_groups(candidate, fresh, refreshed_sources, cache)
    else:
        # Fetch fresh data (cold cache)
        package_data.update(_fetch_fresh_signals(candidate, github_url, config))

        # Save to cache
        try:
            _save_to_cache(candidate, package_data, cache)
        except Exception as e:
            logger.warning(f"Failed to save cache for {candidate.name}: {e}")

    package_data["data_quality"] = _build_data_quality(
        package_data, snapshot, refreshed_sources, now
    )
    return package_data


def _fetch_fresh_signals(
    candidate: PackageCandidate,
    github_url: str,
    config: dict[str, Any],
    sources: set[str] | None = None,
) -> dict[str, Any]:
    """Fetch fresh signals from APIs.

    Args:
        candidate: package candidate from registry
        github_url: verified GitHub URL
        config: system configuration
        sources: when given, fetch only these sources ("github", "deps_dev",
            "downloads"); ``None`` fetches all (cold-cache path).

    Returns:
        Dictionary of collected signals
    """
    signals: dict[str, Any] = {}
    want_github = sources is None or "github" in sources
    want_deps = sources is None or "deps_dev" in sources
    want_downloads = sources is None or "downloads" in sources

    # Get GitHub signals — split into per-group try blocks so a failure in one
    # signal-group (MTTR, commit-regularity) does not lose the others, and the
    # log message identifies which group broke for operator triage.
    github_token = os.getenv("GITHUB_TOKEN")
    if github_token and want_github and not _github_circuit_open():
        github_client: GitHubClient | None = None
        repository = None
        repo_slug = github_url

        try:
            github_client = GitHubClient(
                token=github_token, stagger_ms=config["github"]["stagger_interval_ms"]
            )
            parsed = github_client.parse_github_url(github_url)
            if parsed:
                owner, repo = parsed
                repo_slug = f"{owner}/{repo}"
                repository = github_client.github.get_repo(repo_slug)
        except Exception as e:
            _maybe_trip_github_circuit(e)
            logger.warning(
                f"GitHub {SIGNAL_GROUP_STARS_FORKS} fetch failed for %s: %s", repo_slug, e
            )

        if github_client is not None and repository is not None:
            try:
                star_count = repository.stargazers_count or 0
                fork_count = repository.forks_count or 0
                signals["star_count"] = star_count
                signals["fork_count"] = fork_count
                signals["fork_to_star_ratio"] = (
                    fork_count / star_count if (fork_count and star_count) else 0
                )
                signals["open_issue_count"] = repository.open_issues_count
                if repository.updated_at:
                    signals["days_since_last_commit"] = (
                        datetime.now(timezone.utc) - repository.updated_at
                    ).days
            except Exception as e:
                logger.warning(
                    f"GitHub {SIGNAL_GROUP_STARS_FORKS} fetch failed for %s: %s", repo_slug, e
                )

            try:
                mttr_median, mttr_mad, mttr_state, closed_count = github_client._calculate_mttr(
                    repository,
                    config["github"]["issues_lookback_months"],
                    config["github"]["issues_max_pages"],
                )
                signals["mttr_median_days"] = mttr_median
                signals["mttr_mad"] = mttr_mad
                signals["mttr_state"] = mttr_state
                signals["closed_issues_last_year"] = closed_count
            except Exception as e:
                _maybe_trip_github_circuit(e)
                logger.warning(f"GitHub {SIGNAL_GROUP_MTTR} fetch failed for %s: %s", repo_slug, e)

            try:
                weekly_cv, committer_count = github_client._calculate_commit_regularity(
                    repository, config["github"]["commits_lookback_weeks"]
                )
                signals["weekly_commit_cv"] = weekly_cv
                signals["recent_committer_count"] = committer_count
            except Exception as e:
                logger.warning(
                    f"GitHub {SIGNAL_GROUP_COMMIT_REGULARITY} fetch failed for %s: %s", repo_slug, e
                )

    # Get deps.dev data — split into per-group try blocks so a failure parsing
    # one section (e.g. dep-health) does not drop the others (e.g. version-graph),
    # and the log identifies which group broke for operator triage.
    deps_data: DepsDevData | None = None
    if want_deps:
        try:
            with DepsDevClient() as deps_client:
                deps_data = deps_client.get_package_data(candidate.name, candidate.registry)
        except Exception as e:
            logger.warning(
                f"deps.dev {SIGNAL_GROUP_VERSION_GRAPH} fetch failed for %s: %s", candidate.name, e
            )

    if deps_data:
        try:
            signals["first_release_date"] = deps_data.first_release_date
            signals["latest_version"] = deps_data.latest_version
            signals["release_cv"] = deps_data.release_cv
            signals["major_versions_per_year"] = deps_data.major_versions_per_year
            signals["reverse_dep_count"] = deps_data.reverse_dep_count

            latest_published = _latest_stable_published_at(deps_data)
            if latest_published is not None:
                signals["days_since_compatible_release"] = (
                    datetime.now(timezone.utc) - latest_published
                ).days
        except Exception as e:
            logger.warning(
                f"deps.dev {SIGNAL_GROUP_VERSION_GRAPH} fetch failed for %s: %s", candidate.name, e
            )

        if deps_data.dependency_info:
            try:
                signals["direct_dep_count"] = deps_data.dependency_info.direct_count
                signals["vulnerable_dep_count"] = deps_data.dependency_info.vulnerable_count
                signals["deprecated_dep_count"] = deps_data.dependency_info.deprecated_count
            except Exception as e:
                logger.warning(
                    f"deps.dev {SIGNAL_GROUP_DEP_HEALTH} fetch failed for %s: %s", candidate.name, e
                )

    # Add download data from candidate
    if want_downloads and candidate.weekly_downloads:
        signals["weekly_downloads"] = candidate.weekly_downloads

    # OpenSSF Scorecard (optional, public, no auth) — part of the github source.
    parsed = parse_github_url(github_url)
    if parsed and want_github:
        owner, repo = parsed
        try:
            with ScorecardClient() as sc:
                result = sc.fetch(owner, repo)
            if result.available:
                signals["scorecard_overall"] = result.overall_score
                signals["scorecard_reliability_bucket"] = result.reliability_bucket
                signals["scorecard_dep_health_bucket"] = result.dep_health_bucket
        except Exception as e:
            logger.info(f"Scorecard fetch skipped for {candidate.name}: {e}")

    return signals


def _latest_stable_published_at(deps_data: DepsDevData) -> datetime | None:
    """Publish date of the latest stable, dated, non-yanked version, if any."""
    if not deps_data.latest_version:
        return None
    versions = deps_data.versions
    if not isinstance(versions, list):
        return None
    for v in versions:
        if getattr(v, "version", None) == deps_data.latest_version:
            pub = getattr(v, "published_at", None)
            if pub is not None:
                return pub
    return None


def evaluate_candidate(
    candidate: PackageCandidate,
    language: str,
    cache: SQLiteCache,
    config: dict[str, Any],
    scorer: PackageScorer,
    explain: bool = False,
    service_note: str | None = None,
) -> ScoredPackage | None:
    """Collect signals + score a single candidate. Returns None if GitHub URL missing."""
    package_data = _collect_package_signals(candidate, language, cache, config, service_note)
    if not package_data:
        return None
    try:
        scored = scorer.score_package(package_data, explain=explain)
        enrich_build_vs_borrow(scored, package_data)
        return scored
    except Exception as e:
        logger.warning(f"Failed to score {candidate.name}: {e}", exc_info=True)
        return None


def _save_to_cache(
    candidate: PackageCandidate, package_data: dict[str, Any], cache: SQLiteCache
) -> None:
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
        scorecard_overall=package_data.get("scorecard_overall"),
        scorecard_reliability_bucket=package_data.get("scorecard_reliability_bucket"),
        scorecard_dep_health_bucket=package_data.get("scorecard_dep_health_bucket"),
        description=package_data.get("description"),
        license=package_data.get("license"),
    )

    # Set refresh timestamps
    now = datetime.now(timezone.utc)
    snapshot.downloads_refreshed_at = now
    snapshot.repo_refreshed_at = now
    snapshot.mttr_refreshed_at = now
    snapshot.regularity_refreshed_at = now
    snapshot.version_refreshed_at = now
    snapshot.dep_health_refreshed_at = now

    cache.set(snapshot)
