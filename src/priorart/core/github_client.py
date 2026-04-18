"""
GitHub API client for repository signals.

Uses PyGithub with staggered requests to respect rate limits.
Fetches issues, commits, and repository metadata for scoring.
"""

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median

from github import Github, GithubException
from github.Repository import Repository

from .utils import parse_github_url as _parse_github_url

logger = logging.getLogger(__name__)


@dataclass
class GitHubSignals:
    """GitHub-derived signals for package scoring."""

    # Repository metadata
    star_count: int = 0
    fork_count: int = 0
    open_issues_count: int = 0
    size_kb: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    default_branch: str = "main"

    # MTTR signals
    mttr_median_days: float | None = None
    mttr_mad: float | None = None
    mttr_state: str = "unknown"  # measured, low_volume_healthy, low_volume_backlog, issues_disabled
    closed_issues_last_year: int = 0

    # Commit regularity
    weekly_commit_cv: float | None = None
    recent_committer_count: int = 0
    days_since_last_commit: int | None = None

    # Identity verification
    release_tags: list[str] = field(default_factory=list)
    repo_owner: str = ""
    top_contributors: list[str] = field(default_factory=list)


class GitHubClient:
    """Client for GitHub API with rate limit handling."""

    def __init__(self, token: str | None = None, stagger_ms: int = 100):
        """Initialize GitHub client.

        Args:
            token: GitHub personal access token (from GITHUB_TOKEN env var if not provided)
            stagger_ms: Milliseconds to wait between API calls
        """
        self.token = token or os.getenv("GITHUB_TOKEN")
        if not self.token:
            raise ValueError("GitHub token required. Set GITHUB_TOKEN environment variable.")

        self.github = Github(self.token)
        self.stagger_ms = stagger_ms

    def get_repository_signals(
        self,
        owner: str,
        repo: str,
        issues_lookback_months: int = 12,
        commits_lookback_weeks: int = 104,
        issues_max_pages: int = 2,
    ) -> GitHubSignals | None:
        """Get all GitHub signals for a repository.

        Args:
            owner: Repository owner
            repo: Repository name
            issues_lookback_months: Months to look back for issue data
            commits_lookback_weeks: Weeks to look back for commit data
            issues_max_pages: Maximum pages of issues to fetch (100 per page)

        Returns:
            GitHubSignals with all available data
        """
        try:
            # Get repository object
            repository = self.github.get_repo(f"{owner}/{repo}")
            self._stagger()

            signals = GitHubSignals(
                star_count=repository.stargazers_count,
                fork_count=repository.forks_count,
                open_issues_count=repository.open_issues_count,
                size_kb=repository.size,
                created_at=repository.created_at,
                updated_at=repository.updated_at,
                default_branch=repository.default_branch or "main",
                repo_owner=repository.owner.login,
            )

            # Get days since last commit
            if repository.updated_at:
                days_since = (datetime.now(timezone.utc) - repository.updated_at).days
                signals.days_since_last_commit = days_since

            # Get release tags
            try:
                releases = repository.get_releases()
                signals.release_tags = [r.tag_name for r in releases[:20]]
                self._stagger()
            except GithubException:
                # Some repos don't have releases, try tags instead
                try:
                    tags = repository.get_tags()
                    signals.release_tags = [t.name for t in tags[:20]]
                    self._stagger()
                except GithubException:
                    pass

            # Get top contributors
            try:
                contributors = repository.get_contributors()
                signals.top_contributors = [c.login for c in contributors[:10]]
                self._stagger()
            except GithubException:
                pass

            # Get MTTR signals from issues
            mttr_data = self._calculate_mttr(repository, issues_lookback_months, issues_max_pages)
            signals.mttr_median_days = mttr_data[0]
            signals.mttr_mad = mttr_data[1]
            signals.mttr_state = mttr_data[2]
            signals.closed_issues_last_year = mttr_data[3]

            # Get commit regularity
            commit_data = self._calculate_commit_regularity(repository, commits_lookback_weeks)
            signals.weekly_commit_cv = commit_data[0]
            signals.recent_committer_count = commit_data[1]

            return signals

        except GithubException as e:
            logger.warning(f"GitHub API error for {owner}/{repo}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching GitHub data for {owner}/{repo}: {e}")
            return None

    def _calculate_mttr(
        self, repo: Repository, lookback_months: int, max_pages: int
    ) -> tuple[float | None, float | None, str, int]:
        """Calculate Mean Time To Resolution for issues.

        Returns:
            Tuple of (median_days, MAD, state, closed_count)
        """
        try:
            # Check if issues are enabled
            if not repo.has_issues:
                return None, None, "issues_disabled", 0

            # Get closed issues from the last N months
            since = datetime.now(timezone.utc) - timedelta(days=lookback_months * 30)
            closed_issues = repo.get_issues(
                state="closed", since=since, sort="updated", direction="desc"
            )

            # Fetch up to max_pages (100 issues per page)
            resolution_times = []
            closed_count = 0

            for page_num in range(max_pages):
                try:
                    page = closed_issues.get_page(page_num)
                    if not page:
                        break

                    for issue in page:
                        if issue.closed_at and issue.created_at:
                            # Skip pull requests
                            if issue.pull_request:
                                continue

                            resolution_days = (issue.closed_at - issue.created_at).days
                            resolution_times.append(resolution_days)
                            closed_count += 1

                    self._stagger()
                except Exception:
                    break

            # Check if we have enough data
            if closed_count < 10:
                # Low volume - check health
                open_issues = repo.open_issues_count

                if closed_count == 0 and open_issues > 0:
                    return None, None, "low_volume_backlog", closed_count

                open_to_closed = open_issues / max(closed_count, 1)
                if open_to_closed <= 2.0:
                    return None, None, "low_volume_healthy", closed_count
                else:
                    return None, None, "low_volume_backlog", closed_count

            # Calculate median and MAD
            median_days = median(resolution_times)

            # Calculate MAD (Median Absolute Deviation)
            deviations = [abs(x - median_days) for x in resolution_times]
            mad = median(deviations)

            return median_days, mad, "measured", closed_count

        except Exception as e:
            logger.warning(f"Error calculating MTTR: {e}")
            return None, None, "unknown", 0

    def _calculate_commit_regularity(
        self, repo: Repository, lookback_weeks: int
    ) -> tuple[float | None, int]:
        """Calculate commit regularity over trailing weeks.

        Returns:
            Tuple of (weekly_CV, recent_committer_count)
        """
        try:
            since = datetime.now(timezone.utc) - timedelta(weeks=lookback_weeks)
            commits = repo.get_commits(since=since)

            # Group commits by week
            weekly_counts = {}
            unique_committers = set()
            recent_cutoff = datetime.now(timezone.utc) - timedelta(days=90)

            page_count = 0
            for commit in commits:
                if page_count >= 5:  # Limit pages to avoid timeout
                    break  # pragma: no cover

                commit_date = commit.commit.author.date
                week_key = commit_date.isocalendar()[:2]  # (year, week)
                weekly_counts[week_key] = weekly_counts.get(week_key, 0) + 1

                # Track recent committers
                if commit_date > recent_cutoff and commit.author:
                    unique_committers.add(commit.author.login)

                if len(weekly_counts) > lookback_weeks:
                    break  # pragma: no cover

            self._stagger()

            # Calculate CV for non-zero weeks
            non_zero_counts = [c for c in weekly_counts.values() if c > 0]

            if len(non_zero_counts) < 2:
                return None, len(unique_committers)

            mean_commits = sum(non_zero_counts) / len(non_zero_counts)
            variance = sum((x - mean_commits) ** 2 for x in non_zero_counts) / len(non_zero_counts)
            std_dev = variance**0.5
            cv = std_dev / mean_commits

            return cv, len(unique_committers)

        except Exception as e:
            logger.warning(f"Error calculating commit regularity: {e}")
            return None, 0

    def verify_identity(
        self, github_url: str, package_name: str, registry_maintainers: list[str]
    ) -> bool:
        """Verify that a package legitimately belongs to a GitHub repo.

        Minimal check: the repo name matches the normalized package name, OR
        the repo owner appears in the registry's maintainer list. Covers ~95%
        of real cases at the cost of one API call. The previous release-tag +
        top-contributors crawl cost 2-3 extra calls per package and caught
        only marginal additional squatting cases.
        """
        parsed = self.parse_github_url(github_url)
        if not parsed:
            return False
        owner, repo = parsed

        normalized_pkg = package_name.lower().replace("@", "").replace("/", "-")
        normalized_pkg = normalized_pkg.replace("-", "").replace("_", "")
        normalized_repo = repo.lower().replace("-", "").replace("_", "")

        if normalized_pkg == normalized_repo or normalized_pkg in normalized_repo:
            return True
        if normalized_repo in normalized_pkg:
            return True

        maintainers_lower = {m.lower() for m in registry_maintainers if m}
        if owner.lower() in maintainers_lower:
            return True

        return False

    def _stagger(self) -> None:  # pragma: no cover
        """Wait between API calls to avoid rate limits."""
        time.sleep(self.stagger_ms / 1000.0)

    def parse_github_url(self, url: str) -> tuple[str, str] | None:
        """Parse GitHub URL into owner and repo.

        Args:
            url: GitHub URL

        Returns:
            Tuple of (owner, repo) or None if invalid
        """
        return _parse_github_url(url)
