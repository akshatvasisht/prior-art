"""Tests for github_client.py (parse_github_url and verify_identity logic)."""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from github import GithubException

from priorart.core.github_client import GitHubClient, GitHubSignals

# --- parse_github_url ---


class TestParseGithubUrl:
    """Test URL parsing without needing a real token."""

    def setup_method(self):
        # Bypass __init__ to avoid requiring GITHUB_TOKEN
        self.client = GitHubClient.__new__(GitHubClient)

    def test_standard_url(self):
        assert self.client.parse_github_url("https://github.com/psf/requests") == (
            "psf",
            "requests",
        )

    def test_trailing_slash(self):
        assert self.client.parse_github_url("https://github.com/psf/requests/") == (
            "psf",
            "requests",
        )

    def test_git_suffix(self):
        assert self.client.parse_github_url("https://github.com/owner/repo.git") == (
            "owner",
            "repo",
        )

    def test_subpath(self):
        result = self.client.parse_github_url("https://github.com/owner/repo/tree/main")
        assert result == ("owner", "repo")

    def test_invalid_http(self):
        assert self.client.parse_github_url("http://github.com/owner/repo") is None

    def test_invalid_not_github(self):
        assert self.client.parse_github_url("https://gitlab.com/owner/repo") is None

    def test_invalid_no_repo(self):
        assert self.client.parse_github_url("https://github.com/owner") is None

    def test_empty(self):
        assert self.client.parse_github_url("") is None


# --- verify_identity ---


class TestVerifyIdentity:
    """Test identity verification with mocked PyGithub."""

    def setup_method(self):
        self.client = GitHubClient.__new__(GitHubClient)
        self.client.github = MagicMock()
        self.client.stagger_ms = 0

    def _mock_repo(self, name="requests", owner="psf", release_tags=None, contributor_logins=None):
        repo = MagicMock()
        repo.name = name
        repo.owner.login = owner

        releases = []
        for tag in release_tags or []:
            r = MagicMock()
            r.tag_name = tag
            releases.append(r)
        repo.get_releases.return_value = releases

        contributors = []
        for login in contributor_logins or []:
            c = MagicMock()
            c.login = login
            contributors.append(c)
        repo.get_contributors.return_value = contributors

        self.client.github.get_repo.return_value = repo
        return repo

    def test_repo_name_match(self):
        self._mock_repo(name="requests", owner="psf")
        assert (
            self.client.verify_identity("https://github.com/psf/requests", "requests", []) is True
        )

    def test_release_tag_match(self):
        self._mock_repo(name="some-repo", owner="org", release_tags=["v1.0.0", "mylib-v2.0.0"])
        assert self.client.verify_identity("https://github.com/org/some-repo", "mylib", []) is True

    def test_maintainer_owner_match(self):
        self._mock_repo(name="unrelated-name", owner="psf")
        assert (
            self.client.verify_identity(
                "https://github.com/psf/unrelated-name", "requests", ["psf"]
            )
            is True
        )

    def test_maintainer_contributor_match(self):
        self._mock_repo(name="unrelated", owner="org", contributor_logins=["alice", "bob"])
        assert (
            self.client.verify_identity("https://github.com/org/unrelated", "something", ["bob"])
            is True
        )

    def test_no_match_returns_false(self):
        self._mock_repo(name="totally-different", owner="someone-else")
        assert (
            self.client.verify_identity(
                "https://github.com/someone-else/totally-different",
                "my-package",
                ["unrelated-maintainer"],
            )
            is False
        )

    def test_invalid_url_returns_false(self):
        assert self.client.verify_identity("not-a-url", "pkg", []) is False

    def test_release_check_exception_graceful(self):
        """GithubException in get_releases during verify_identity is caught."""
        repo = self._mock_repo(name="unrelated", owner="org")
        repo.get_releases.side_effect = GithubException(403, {}, None)

        # No repo name match, no release match, but maintainer matches
        assert (
            self.client.verify_identity("https://github.com/org/unrelated", "pkg", ["org"]) is True
        )

    def test_contributor_check_exception_graceful(self):
        """GithubException in get_contributors during verify_identity is caught."""
        repo = self._mock_repo(name="unrelated", owner="someone")
        repo.get_contributors.side_effect = GithubException(403, {}, None)

        # Maintainer doesn't match owner, contributors fail → no maintainer match
        assert (
            self.client.verify_identity(
                "https://github.com/someone/unrelated", "pkg", ["unknown-person"]
            )
            is False
        )

    def test_verify_identity_unexpected_exception(self):
        """Unexpected exception in verify_identity returns False."""
        self.client.github.get_repo.side_effect = RuntimeError("network")

        assert self.client.verify_identity("https://github.com/owner/repo", "pkg", []) is False


# --- GitHubSignals dataclass ---


def test_github_signals_defaults():
    """Verify default values and __post_init__ list initialization."""
    signals = GitHubSignals()
    assert signals.star_count == 0
    assert signals.release_tags == []
    assert signals.top_contributors == []
    assert signals.mttr_state == "unknown"
    assert signals.days_since_last_commit is None


# --- __init__ ---


def test_init_missing_token():
    """GitHubClient raises ValueError without a token."""
    with patch.dict(os.environ, {}, clear=True):
        # Also clear GITHUB_TOKEN specifically
        os.environ.pop("GITHUB_TOKEN", None)
        with pytest.raises(ValueError, match="GitHub token required"):
            GitHubClient(token=None)


# --- get_repository_signals ---


class TestGetRepositorySignals:
    def setup_method(self):
        self.client = GitHubClient.__new__(GitHubClient)
        self.client.github = MagicMock()
        self.client.stagger_ms = 0
        self.client.token = "fake"

    def _mock_repo(self):
        repo = MagicMock()
        repo.stargazers_count = 50000
        repo.forks_count = 9000
        repo.open_issues_count = 250
        repo.size = 12345
        repo.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        repo.updated_at = datetime.now(timezone.utc) - timedelta(days=5)
        repo.default_branch = "main"
        repo.owner.login = "psf"
        repo.has_issues = True

        # Releases
        release = MagicMock()
        release.tag_name = "v2.31.0"
        repo.get_releases.return_value = [release]

        # Contributors
        contrib = MagicMock()
        contrib.login = "kennethreitz"
        repo.get_contributors.return_value = [contrib]

        self.client.github.get_repo.return_value = repo
        return repo

    def test_success(self):
        """Full success path returns populated GitHubSignals."""
        self._mock_repo()

        with patch.object(self.client, "_calculate_mttr", return_value=(3.5, 1.2, "measured", 50)):
            with patch.object(self.client, "_calculate_commit_regularity", return_value=(0.3, 15)):
                signals = self.client.get_repository_signals("psf", "requests")

        assert signals is not None
        assert signals.star_count == 50000
        assert signals.fork_count == 9000
        assert signals.repo_owner == "psf"
        assert signals.release_tags == ["v2.31.0"]
        assert signals.top_contributors == ["kennethreitz"]
        assert signals.mttr_median_days == 3.5
        assert signals.mttr_state == "measured"
        assert signals.weekly_commit_cv == 0.3
        assert signals.recent_committer_count == 15

    def test_github_error_returns_none(self):
        """GithubException from get_repo returns None."""
        self.client.github.get_repo.side_effect = GithubException(
            404, {"message": "Not Found"}, None
        )

        result = self.client.get_repository_signals("owner", "nonexistent")
        assert result is None

    def test_release_fallback_to_tags(self):
        """When get_releases raises, falls back to get_tags."""
        repo = self._mock_repo()

        # Releases fail
        repo.get_releases.side_effect = GithubException(403, {"message": "Forbidden"}, None)

        # Tags work
        tag = MagicMock()
        tag.name = "v1.0.0"
        repo.get_tags.return_value = [tag]

        with patch.object(self.client, "_calculate_mttr", return_value=(None, None, "unknown", 0)):
            with patch.object(self.client, "_calculate_commit_regularity", return_value=(None, 0)):
                signals = self.client.get_repository_signals("owner", "repo")

        assert signals is not None
        assert signals.release_tags == ["v1.0.0"]

    def test_contributors_exception_graceful(self):
        """Contributors exception is handled gracefully."""
        repo = self._mock_repo()
        repo.get_contributors.side_effect = GithubException(403, {"message": "Forbidden"}, None)

        with patch.object(self.client, "_calculate_mttr", return_value=(None, None, "unknown", 0)):
            with patch.object(self.client, "_calculate_commit_regularity", return_value=(None, 0)):
                signals = self.client.get_repository_signals("owner", "repo")

        assert signals is not None
        assert signals.top_contributors == []

    def test_unexpected_error_returns_none(self):
        """Unexpected non-GithubException also returns None."""
        self.client.github.get_repo.side_effect = RuntimeError("network failure")

        result = self.client.get_repository_signals("owner", "repo")
        assert result is None


# --- _calculate_mttr ---


class TestCalculateMTTR:
    def setup_method(self):
        self.client = GitHubClient.__new__(GitHubClient)
        self.client.stagger_ms = 0

    def test_issues_disabled(self):
        """Returns issues_disabled when repo has issues turned off."""
        repo = MagicMock()
        repo.has_issues = False

        median, mad, state, count = self.client._calculate_mttr(repo, 12, 2)
        assert state == "issues_disabled"
        assert count == 0

    def test_measured_state(self):
        """Returns measured with correct median/MAD when enough issues."""
        repo = MagicMock()
        repo.has_issues = True
        repo.open_issues_count = 10

        # Create 15 mock issues with known resolution times
        now = datetime.now(timezone.utc)
        issues = []
        for i in range(15):
            issue = MagicMock()
            issue.pull_request = None  # Not a PR
            issue.created_at = now - timedelta(days=30 + i)
            issue.closed_at = now - timedelta(days=20 + i)  # 10 days resolution
            issues.append(issue)

        page_mock = MagicMock()
        page_mock.get_page.side_effect = [issues, []]
        repo.get_issues.return_value = page_mock

        median, mad, state, count = self.client._calculate_mttr(repo, 12, 2)

        assert state == "measured"
        assert count == 15
        assert median == 10.0  # All issues resolved in 10 days
        assert mad == 0.0  # No deviation

    def test_low_volume_healthy(self):
        """Low volume with good open/closed ratio is healthy."""
        repo = MagicMock()
        repo.has_issues = True
        repo.open_issues_count = 3  # Low open issues

        # Only 5 issues (< 10)
        now = datetime.now(timezone.utc)
        issues = []
        for _i in range(5):
            issue = MagicMock()
            issue.pull_request = None
            issue.created_at = now - timedelta(days=30)
            issue.closed_at = now - timedelta(days=25)
            issues.append(issue)

        page_mock = MagicMock()
        page_mock.get_page.side_effect = [issues, []]
        repo.get_issues.return_value = page_mock

        median, mad, state, count = self.client._calculate_mttr(repo, 12, 2)

        assert state == "low_volume_healthy"
        assert count == 5

    def test_low_volume_backlog(self):
        """Low volume with high open/closed ratio is backlog."""
        repo = MagicMock()
        repo.has_issues = True
        repo.open_issues_count = 100  # Many open issues

        # Only 3 closed (< 10)
        now = datetime.now(timezone.utc)
        issues = []
        for _i in range(3):
            issue = MagicMock()
            issue.pull_request = None
            issue.created_at = now - timedelta(days=30)
            issue.closed_at = now - timedelta(days=25)
            issues.append(issue)

        page_mock = MagicMock()
        page_mock.get_page.side_effect = [issues, []]
        repo.get_issues.return_value = page_mock

        median, mad, state, count = self.client._calculate_mttr(repo, 12, 2)

        assert state == "low_volume_backlog"
        assert count == 3


# --- _calculate_commit_regularity ---


class TestCalculateCommitRegularity:
    def setup_method(self):
        self.client = GitHubClient.__new__(GitHubClient)
        self.client.stagger_ms = 0

    def test_regularity_calculated(self):
        """Returns CV and committer count from commit data."""
        repo = MagicMock()

        now = datetime.now(timezone.utc)
        commits = []
        for week in range(10):
            for day in range(3):  # 3 commits per week
                commit = MagicMock()
                commit.commit.author.date = now - timedelta(weeks=week, days=day)
                commit.author = MagicMock()
                commit.author.login = f"dev{week % 3}"
                commits.append(commit)

        repo.get_commits.return_value = commits

        cv, committer_count = self.client._calculate_commit_regularity(repo, 104)

        assert cv is not None
        assert cv >= 0.0
        assert committer_count >= 1

    def test_regularity_exception_returns_default(self):
        """Exception in commit regularity returns (None, 0)."""
        repo = MagicMock()
        repo.get_commits.side_effect = RuntimeError("API failure")

        cv, count = self.client._calculate_commit_regularity(repo, 104)
        assert cv is None
        assert count == 0

    def test_regularity_single_week(self):
        """Fewer than 2 weeks returns None CV."""
        repo = MagicMock()
        now = datetime.now(timezone.utc)

        commit = MagicMock()
        commit.commit.author.date = now
        commit.author = MagicMock()
        commit.author.login = "dev"
        repo.get_commits.return_value = [commit]

        cv, count = self.client._calculate_commit_regularity(repo, 104)
        assert cv is None


# --- MTTR edge cases ---


class TestMTTREdgeCases:
    def setup_method(self):
        self.client = GitHubClient.__new__(GitHubClient)
        self.client.stagger_ms = 0

    def test_mttr_exception_returns_unknown(self):
        """General exception in MTTR returns unknown state."""
        repo = MagicMock()
        repo.has_issues = True
        repo.get_issues.side_effect = RuntimeError("API down")

        median, mad, state, count = self.client._calculate_mttr(repo, 12, 2)
        assert state == "unknown"
        assert count == 0

    def test_mttr_zero_closed_with_open_issues(self):
        """Zero closed issues with open issues → low_volume_backlog."""
        repo = MagicMock()
        repo.has_issues = True
        repo.open_issues_count = 50

        page_mock = MagicMock()
        page_mock.get_page.return_value = []  # No issues
        repo.get_issues.return_value = page_mock

        median, mad, state, count = self.client._calculate_mttr(repo, 12, 2)
        assert state == "low_volume_backlog"

    def test_mttr_skips_pull_requests(self):
        """PRs are filtered out from MTTR calculation."""
        repo = MagicMock()
        repo.has_issues = True
        repo.open_issues_count = 5

        now = datetime.now(timezone.utc)
        issues = []

        # 12 real issues
        for _i in range(12):
            issue = MagicMock()
            issue.pull_request = None
            issue.created_at = now - timedelta(days=20)
            issue.closed_at = now - timedelta(days=15)  # 5 day resolution
            issues.append(issue)

        # 3 pull requests (should be skipped)
        for _i in range(3):
            pr = MagicMock()
            pr.pull_request = MagicMock()  # truthy = is a PR
            pr.created_at = now - timedelta(days=10)
            pr.closed_at = now - timedelta(days=9)
            issues.append(pr)

        page_mock = MagicMock()
        page_mock.get_page.side_effect = [issues, []]
        repo.get_issues.return_value = page_mock

        median, mad, state, count = self.client._calculate_mttr(repo, 12, 2)
        assert state == "measured"
        assert count == 12  # Only real issues counted
        assert median == 5.0


# --- __init__ success ---


def test_init_success():
    """GitHubClient.__init__ succeeds with valid token."""
    with patch("priorart.core.github_client.Github") as mock_github:
        client = GitHubClient(token="test-token")

    assert client.token == "test-token"
    assert client.stagger_ms == 100
    mock_github.assert_called_once_with("test-token")


# --- Both releases and tags fail ---


class TestReleasesAndTagsBothFail:
    def setup_method(self):
        self.client = GitHubClient.__new__(GitHubClient)
        self.client.github = MagicMock()
        self.client.stagger_ms = 0
        self.client.token = "fake"

    def test_both_releases_and_tags_fail(self):
        """When both get_releases and get_tags raise, release_tags stays empty."""
        repo = MagicMock()
        repo.stargazers_count = 100
        repo.forks_count = 10
        repo.open_issues_count = 5
        repo.size = 1000
        repo.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        repo.updated_at = datetime.now(timezone.utc)
        repo.default_branch = "main"
        repo.owner.login = "owner"
        repo.has_issues = True

        repo.get_releases.side_effect = GithubException(403, {}, None)
        repo.get_tags.side_effect = GithubException(403, {}, None)

        contrib = MagicMock()
        contrib.login = "dev"
        repo.get_contributors.return_value = [contrib]

        self.client.github.get_repo.return_value = repo

        with patch.object(self.client, "_calculate_mttr", return_value=(None, None, "unknown", 0)):
            with patch.object(self.client, "_calculate_commit_regularity", return_value=(None, 0)):
                signals = self.client.get_repository_signals("owner", "repo")

        assert signals is not None
        assert signals.release_tags == []


# --- MTTR page fetch exception ---


class TestMTTRPageException:
    def setup_method(self):
        self.client = GitHubClient.__new__(GitHubClient)
        self.client.stagger_ms = 0

    def test_page_fetch_exception_breaks(self):
        """Exception during page fetch breaks the page loop."""
        repo = MagicMock()
        repo.has_issues = True
        repo.open_issues_count = 5

        now = datetime.now(timezone.utc)
        # First page has 5 issues
        issues = []
        for _i in range(5):
            issue = MagicMock()
            issue.pull_request = None
            issue.created_at = now - timedelta(days=30)
            issue.closed_at = now - timedelta(days=25)
            issues.append(issue)

        page_mock = MagicMock()
        # First page succeeds, second page raises
        page_mock.get_page.side_effect = [issues, RuntimeError("page error")]
        repo.get_issues.return_value = page_mock

        median, mad, state, count = self.client._calculate_mttr(repo, 12, 2)

        # 5 issues < 10, so low_volume path
        assert state == "low_volume_healthy"
        assert count == 5
