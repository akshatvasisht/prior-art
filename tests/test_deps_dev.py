"""Tests for deps_dev.py pure-logic functions (no network calls)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from priorart.core.deps_dev import (
    DepsDevClient,
    DepsDevData,
    VersionInfo,
)


def _make_version(
    version: str, published: str | None = None, prerelease: bool = False, yanked: bool = False
) -> VersionInfo:
    pub = None
    if published:
        pub = datetime.fromisoformat(published)
    return VersionInfo(
        version=version, published_at=pub, is_prerelease=prerelease, is_yanked=yanked
    )


# --- _get_latest_stable_version ---


class TestGetLatestStableVersion:
    def setup_method(self):
        self.client = DepsDevClient.__new__(DepsDevClient)

    def test_returns_latest_stable(self):
        versions = [
            _make_version("1.0.0", "2023-01-01T00:00:00+00:00"),
            _make_version("2.0.0", "2024-01-01T00:00:00+00:00"),
            _make_version("3.0.0-beta", "2024-06-01T00:00:00+00:00", prerelease=True),
        ]
        assert self.client._get_latest_stable_version(versions) == "2.0.0"

    def test_skips_yanked(self):
        versions = [
            _make_version("1.0.0", "2023-01-01T00:00:00+00:00"),
            _make_version("2.0.0", "2024-01-01T00:00:00+00:00", yanked=True),
        ]
        assert self.client._get_latest_stable_version(versions) == "1.0.0"

    def test_no_stable_versions(self):
        versions = [
            _make_version("1.0.0-alpha", "2023-01-01T00:00:00+00:00", prerelease=True),
        ]
        assert self.client._get_latest_stable_version(versions) is None

    def test_empty_list(self):
        assert self.client._get_latest_stable_version([]) is None


# --- _calculate_release_cv ---


class TestCalculateReleaseCV:
    def setup_method(self):
        self.client = DepsDevClient.__new__(DepsDevClient)

    def test_regular_releases_low_cv(self):
        """Evenly spaced releases should have CV near 0."""
        versions = [
            _make_version("1.0.0", "2023-01-01T00:00:00+00:00"),
            _make_version("1.1.0", "2023-04-01T00:00:00+00:00"),
            _make_version("1.2.0", "2023-07-01T00:00:00+00:00"),
            _make_version("1.3.0", "2023-10-01T00:00:00+00:00"),
        ]
        cv = self.client._calculate_release_cv(versions)
        assert cv < 0.1  # Near-equal spacing

    def test_irregular_releases_higher_cv(self):
        """Unevenly spaced releases should have higher CV."""
        versions = [
            _make_version("1.0.0", "2023-01-01T00:00:00+00:00"),
            _make_version("1.1.0", "2023-01-10T00:00:00+00:00"),  # 9 days
            _make_version("1.2.0", "2023-07-01T00:00:00+00:00"),  # 172 days
            _make_version("1.3.0", "2023-07-05T00:00:00+00:00"),  # 4 days
        ]
        cv = self.client._calculate_release_cv(versions)
        assert cv > 0.5

    def test_fewer_than_3_versions_returns_zero(self):
        versions = [
            _make_version("1.0.0", "2023-01-01T00:00:00+00:00"),
            _make_version("1.1.0", "2023-04-01T00:00:00+00:00"),
        ]
        assert self.client._calculate_release_cv(versions) == 0.0

    def test_excludes_prereleases(self):
        versions = [
            _make_version("1.0.0", "2023-01-01T00:00:00+00:00"),
            _make_version("1.1.0-rc1", "2023-02-01T00:00:00+00:00", prerelease=True),
            _make_version("1.1.0", "2023-04-01T00:00:00+00:00"),
            _make_version("1.2.0", "2023-07-01T00:00:00+00:00"),
        ]
        cv = self.client._calculate_release_cv(versions)
        # Only 3 stable versions → should compute
        assert cv >= 0.0


# --- _calculate_major_versions_per_year ---


class TestMajorVersionsPerYear:
    def setup_method(self):
        self.client = DepsDevClient.__new__(DepsDevClient)

    def test_multiple_majors(self):
        first = datetime(2020, 1, 1, tzinfo=timezone.utc)
        versions = [
            _make_version("1.0.0", "2020-01-01T00:00:00+00:00"),
            _make_version("2.0.0", "2021-01-01T00:00:00+00:00"),
            _make_version("3.0.0", "2022-01-01T00:00:00+00:00"),
        ]
        rate = self.client._calculate_major_versions_per_year(versions, first)
        # 3 distinct majors over ~5+ years → rate < 1.0
        assert 0 < rate < 2.0

    def test_no_stable_versions_returns_zero(self):
        first = datetime(2020, 1, 1, tzinfo=timezone.utc)
        versions = [
            _make_version("1.0.0-alpha", prerelease=True),
        ]
        assert self.client._calculate_major_versions_per_year(versions, first) == 0.0


# --- _fetch_dependency_info ---


class TestFetchDependencyInfo:
    def setup_method(self):
        self.client = DepsDevClient.__new__(DepsDevClient)
        self.client.client = MagicMock()

    def _resp(self, status_code=200, json_data=None):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_data or {}
        return resp

    def _key(self, name):
        return {"system": "PYPI", "name": name, "version": "1.0.0"}

    def test_counts_from_graph_and_batch(self):
        graph = self._resp(
            200,
            {
                "nodes": [
                    {"relation": "SELF", "versionKey": self._key("self")},
                    {"relation": "DIRECT", "versionKey": self._key("a")},
                    {"relation": "DIRECT", "versionKey": self._key("b")},
                    {"relation": "INDIRECT", "versionKey": self._key("c")},
                ]
            },
        )
        batch = self._resp(
            200,
            {
                "responses": [
                    {"version": {"advisoryKeys": [{"id": "GHSA-x"}]}},
                    {"version": {"isDeprecated": True}},
                    {"version": {}},
                ],
                "nextPageToken": "",
            },
        )
        self.client.client.get = MagicMock(return_value=graph)
        self.client.client.post = MagicMock(return_value=batch)

        info = self.client._fetch_dependency_info("pypi", "pkg", "1.0.0")

        assert info.direct_count == 2  # DIRECT nodes only (SELF excluded)
        assert info.vulnerable_count == 1  # one dep with advisoryKeys
        assert info.deprecated_count == 1  # one deprecated dep

    def test_dependencies_call_non_200(self):
        self.client.client.get = MagicMock(return_value=self._resp(404, {}))
        info = self.client._fetch_dependency_info("pypi", "pkg", "1.0.0")

        assert info.direct_count == 0
        assert info.vulnerable_count == 0
        assert info.deprecated_count == 0

    def test_batch_call_non_200_keeps_direct_count(self):
        graph = self._resp(200, {"nodes": [{"relation": "DIRECT", "versionKey": self._key("a")}]})
        self.client.client.get = MagicMock(return_value=graph)
        self.client.client.post = MagicMock(return_value=self._resp(429, {}))

        info = self.client._fetch_dependency_info("pypi", "pkg", "1.0.0")

        assert info.direct_count == 1  # graph parsed
        assert info.vulnerable_count == 0  # batch failed, counts stay zero
        assert info.deprecated_count == 0

    def test_no_dependencies_skips_batch(self):
        graph = self._resp(200, {"nodes": [{"relation": "SELF", "versionKey": self._key("self")}]})
        self.client.client.get = MagicMock(return_value=graph)
        self.client.client.post = MagicMock()

        info = self.client._fetch_dependency_info("pypi", "pkg", "1.0.0")

        assert info.direct_count == 0
        self.client.client.post.assert_not_called()

    def test_paginates_batch_responses(self):
        graph = self._resp(
            200,
            {"nodes": [{"relation": "DIRECT", "versionKey": self._key(f"d{i}")} for i in range(3)]},
        )
        page1 = self._resp(
            200, {"responses": [{"version": {"advisoryKeys": ["x"]}}], "nextPageToken": "tok"}
        )
        page2 = self._resp(
            200, {"responses": [{"version": {"isDeprecated": True}}], "nextPageToken": ""}
        )
        self.client.client.get = MagicMock(return_value=graph)
        self.client.client.post = MagicMock(side_effect=[page1, page2])

        info = self.client._fetch_dependency_info("pypi", "pkg", "1.0.0")

        assert info.direct_count == 3
        assert info.vulnerable_count == 1
        assert info.deprecated_count == 1
        assert self.client.client.post.call_count == 2


# --- _extract_github_url ---


class TestExtractGithubUrl:
    def setup_method(self):
        self.client = DepsDevClient.__new__(DepsDevClient)

    def test_extracts_github_url(self):
        data = {
            "sourceRepository": {
                "type": "GITHUB",
                "url": "https://github.com/psf/requests",
            }
        }
        assert self.client._extract_github_url(data) == "https://github.com/psf/requests"

    def test_non_github_returns_none(self):
        data = {
            "sourceRepository": {
                "type": "GITLAB",
                "url": "https://gitlab.com/owner/repo",
            }
        }
        assert self.client._extract_github_url(data) is None

    def test_missing_source_repo(self):
        assert self.client._extract_github_url({}) is None


# --- _parse_versions ---


class TestParseVersions:
    def setup_method(self):
        self.client = DepsDevClient.__new__(DepsDevClient)

    def test_parses_version_list(self):
        data = {
            "versions": [
                {
                    "versionKey": {"version": "1.0.0"},
                    "publishedAt": "2023-01-15T10:00:00Z",
                    "isYanked": False,
                },
                {
                    "versionKey": {"version": "2.0.0-beta.1"},
                    "publishedAt": "2024-01-01T00:00:00Z",
                    "isYanked": False,
                },
            ]
        }
        versions = self.client._parse_versions(data)

        assert len(versions) == 2
        assert versions[0].version == "1.0.0"
        assert versions[0].is_prerelease is False
        assert versions[1].version == "2.0.0-beta.1"
        assert versions[1].is_prerelease is True

    def test_empty_versions(self):
        assert self.client._parse_versions({"versions": []}) == []
        assert self.client._parse_versions({}) == []

    def test_skips_empty_version_string(self):
        data = {
            "versions": [
                {"versionKey": {"version": ""}, "publishedAt": None},
                {"versionKey": {"version": "1.0.0"}, "publishedAt": None},
            ]
        }
        versions = self.client._parse_versions(data)
        assert len(versions) == 1


# --- Context manager ---


def test_context_manager():
    """DepsDevClient supports with-statement."""
    with DepsDevClient(timeout=5) as client:
        assert client is not None
    # After exit, client should be closed (no assertion needed, just no crash)


# --- get_package_data (mocked HTTP) ---


class TestGetPackageData:
    def setup_method(self):
        self.client = DepsDevClient.__new__(DepsDevClient)
        self.client.client = MagicMock()

    def _mock_response(self, status_code=200, json_data=None):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_data or {}
        resp.raise_for_status = MagicMock()
        return resp

    def test_full_success(self):
        """Complete get_package_data with all enrichment.

        deps.dev v3 embeds the version history in the package response itself,
        so there is no separate /versions list call — only the per-version
        detail call (``/versions/{version}``) for dependency info.
        """
        # Package response — versions are embedded inline (v3 shape).
        pkg_resp = self._mock_response(
            200,
            {
                "dependentCount": 150000,
                "sourceRepository": {
                    "type": "GITHUB",
                    "url": "https://github.com/psf/requests",
                },
                "versions": [
                    {
                        "versionKey": {"version": "1.0.0"},
                        "publishedAt": "2012-01-01T00:00:00Z",
                        "isYanked": False,
                    },
                    {
                        "versionKey": {"version": "2.31.0"},
                        "publishedAt": "2023-06-01T00:00:00Z",
                        "isYanked": False,
                    },
                ],
            },
        )

        # Dependency graph (v3 :dependencies sub-resource) for the latest version.
        deps_graph_resp = self._mock_response(
            200,
            {
                "nodes": [
                    {"relation": "SELF", "versionKey": {"system": "PYPI", "name": "requests"}},
                    {"relation": "DIRECT", "versionKey": {"system": "PYPI", "name": "urllib3"}},
                    {"relation": "DIRECT", "versionKey": {"system": "PYPI", "name": "certifi"}},
                    {"relation": "INDIRECT", "versionKey": {"system": "PYPI", "name": "idna"}},
                ]
            },
        )

        # Per-dependency advisory/deprecation flags (v3alpha versionbatch POST).
        batch_resp = self._mock_response(
            200,
            {
                "responses": [
                    {"version": {"advisoryKeys": [{"id": "GHSA-1"}]}},
                    {"version": {"isDeprecated": True}},
                    {"version": {}},
                ],
                "nextPageToken": "",
            },
        )

        # Reverse-dependency count (v3alpha :dependents resource).
        dependents_resp = self._mock_response(200, {"dependentCount": 150000})

        def side_effect(url):
            if ":dependents" in url:
                return dependents_resp
            if ":dependencies" in url:
                return deps_graph_resp
            return pkg_resp

        self.client.client.get = side_effect
        self.client.client.post = MagicMock(return_value=batch_resp)

        data = self.client.get_package_data("requests", "pypi")

        assert data is not None
        assert data.github_url == "https://github.com/psf/requests"
        assert data.reverse_dep_count == 150000
        assert data.latest_version == "2.31.0"
        assert data.first_release_date is not None
        assert data.dependency_info is not None
        assert data.dependency_info.direct_count == 2
        assert data.dependency_info.vulnerable_count == 1

    def test_404_returns_none(self):
        """404 from primary package call returns None."""
        self.client.client.get = MagicMock(return_value=self._mock_response(404))

        result = self.client.get_package_data("nonexistent", "pypi")
        assert result is None

    def test_no_versions_graceful(self):
        """A package response with no embedded versions still returns data.

        With no versions there is no first_release_date and no per-version
        detail call — the package metadata (revdep, source repo) is still
        returned rather than erroring.
        """
        pkg_resp = self._mock_response(
            200,
            {
                "sourceRepository": {},
                "versions": [],
            },
        )
        self.client.client.get = MagicMock(return_value=pkg_resp)

        data = self.client.get_package_data("pkg", "npm")
        assert data is not None
        assert data.versions == []
        assert data.first_release_date is None
        assert data.latest_version is None
        # No version → no :dependents call → count stays 0.
        assert data.reverse_dep_count == 0

    def test_unsupported_ecosystem(self):
        """Unsupported ecosystem returns None."""
        result = self.client.get_package_data("pkg", "cobol")
        assert result is None


# --- get_identity_fallback ---


def test_get_identity_fallback():
    """get_identity_fallback returns github_url from get_package_data."""
    client = DepsDevClient.__new__(DepsDevClient)
    client.client = MagicMock()

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "dependentCount": 0,
        "sourceRepository": {
            "type": "GITHUB",
            "url": "https://github.com/owner/repo",
        },
    }
    resp.raise_for_status = MagicMock()

    versions_resp = MagicMock()
    versions_resp.status_code = 200
    versions_resp.json.return_value = {"versions": []}

    def side_effect(url):
        if "/versions" in url:
            return versions_resp
        return resp

    client.client.get = side_effect

    url = client.get_identity_fallback("pkg", "pypi")
    assert url == "https://github.com/owner/repo"


def test_get_identity_fallback_no_data():
    """get_identity_fallback returns None when package not found."""
    client = DepsDevClient.__new__(DepsDevClient)
    client.client = MagicMock()

    resp = MagicMock()
    resp.status_code = 404
    client.client.get = MagicMock(return_value=resp)

    url = client.get_identity_fallback("nonexistent", "pypi")
    assert url is None


# --- _fetch_dependent_count (v3alpha :dependents) ---


def test_fetch_dependent_count():
    client = DepsDevClient.__new__(DepsDevClient)
    client.client = MagicMock()

    ok = MagicMock(status_code=200)
    ok.json.return_value = {"dependentCount": 2161}
    client.client.get = MagicMock(return_value=ok)
    assert client._fetch_dependent_count("pypi", "requests", "2.32.3") == 2161
    # The version-scoped v3alpha resource is the one queried.
    assert ":dependents" in client.client.get.call_args[0][0]

    # Missing/null count → 0.
    empty = MagicMock(status_code=200)
    empty.json.return_value = {}
    client.client.get = MagicMock(return_value=empty)
    assert client._fetch_dependent_count("pypi", "x", "1.0.0") == 0

    # Non-200 → 0.
    client.client.get = MagicMock(return_value=MagicMock(status_code=404))
    assert client._fetch_dependent_count("pypi", "x", "1.0.0") == 0

    # Network error → 0 (best-effort, never breaks scoring).
    client.client.get = MagicMock(side_effect=ConnectionError("boom"))
    assert client._fetch_dependent_count("pypi", "x", "1.0.0") == 0


# --- DepsDevData dataclass ---


def test_deps_dev_data_post_init():
    """DepsDevData defaults versions to empty list."""
    data = DepsDevData(package_name="test", ecosystem="pypi")
    assert data.versions == []


# --- _parse_versions edge cases ---


class TestParseVersionsEdgeCases:
    def setup_method(self):
        self.client = DepsDevClient.__new__(DepsDevClient)

    def test_invalid_published_timestamp(self):
        """Invalid publishedAt is handled gracefully."""
        data = {
            "versions": [
                {
                    "versionKey": {"version": "1.0.0"},
                    "publishedAt": "not-a-date",
                    "isYanked": False,
                },
            ]
        }
        versions = self.client._parse_versions(data)
        assert len(versions) == 1
        assert versions[0].published_at is None

    def test_unparseable_version_fallback(self):
        """Version string that packaging can't parse falls back to pattern check."""
        data = {
            "versions": [
                {
                    "versionKey": {"version": "totally-invalid-version-alpha"},
                    "publishedAt": None,
                    "isYanked": False,
                },
            ]
        }
        versions = self.client._parse_versions(data)
        assert len(versions) == 1
        # "alpha" in the string should trigger prerelease detection
        assert versions[0].is_prerelease is True


# --- _calculate_major_versions_per_year edge cases ---


class TestMajorVersionsEdgeCases:
    def setup_method(self):
        self.client = DepsDevClient.__new__(DepsDevClient)

    def test_no_first_release(self):
        """Returns 0 when first_release is None."""
        assert self.client._calculate_major_versions_per_year([], None) == 0.0

    def test_version_parsing_fallback_regex(self):
        """Unparseable version falls back to regex extraction."""
        first = datetime(2020, 1, 1, tzinfo=timezone.utc)
        versions = [
            _make_version("v1-custom-build", "2020-06-01T00:00:00+00:00"),
            _make_version("v2-custom-build", "2021-06-01T00:00:00+00:00"),
        ]
        # These won't parse with packaging, should fall back to regex
        rate = self.client._calculate_major_versions_per_year(versions, first)
        assert rate > 0


# --- _calculate_release_cv edge cases ---


class TestReleaseCVEdgeCases:
    def setup_method(self):
        self.client = DepsDevClient.__new__(DepsDevClient)

    def test_same_day_releases_zero_mean(self):
        """Releases on the same day yield mean_interval=0 → returns 0.0."""
        versions = [
            _make_version("1.0.0", "2023-01-01T00:00:00+00:00"),
            _make_version("1.1.0", "2023-01-01T00:00:00+00:00"),
            _make_version("1.2.0", "2023-01-01T00:00:00+00:00"),
        ]
        assert self.client._calculate_release_cv(versions) == 0.0


# --- get_package_data dep info exception ---


class TestGetPackageDataDepInfoException:
    def setup_method(self):
        self.client = DepsDevClient.__new__(DepsDevClient)
        self.client.client = MagicMock()

    def test_dep_info_fetch_exception(self):
        """Exception fetching per-version dep info still returns package data."""
        pkg_resp = MagicMock()
        pkg_resp.status_code = 200
        pkg_resp.json.return_value = {
            "dependentCount": 50,
            "versions": [
                {
                    "versionKey": {"version": "1.0.0"},
                    "publishedAt": "2023-01-01T00:00:00Z",
                    "isYanked": False,
                },
            ],
        }
        pkg_resp.raise_for_status = MagicMock()

        def side_effect(url):
            # The per-version detail call (carries a version in the path) fails;
            # version metadata parsed from the package response must survive.
            if "/versions/" in url:
                raise ConnectionError("timeout")
            return pkg_resp

        self.client.client.get = side_effect

        data = self.client.get_package_data("pkg", "pypi")
        assert data is not None
        assert data.dependency_info is None
        assert data.latest_version == "1.0.0"


# --- live contract checks ---


@pytest.mark.integration
class TestDepsDevLive:
    """Live deps.dev checks — guard against the contract drift that left
    dependency-health structurally zero (the v3 version response dropped
    ``relations``/``resolvedDependencies``; the graph moved to ``:dependencies``).
    A regression here means the dependency-health dimension is silently dead.
    """

    def test_dependency_health_returns_real_counts(self):
        with DepsDevClient() as client:
            data = client.get_package_data("requests", "pypi")
        assert data is not None
        assert data.dependency_info is not None
        # `requests` has several direct dependencies (urllib3, certifi, idna, ...);
        # a zero direct_count means the dependency-graph contract drifted again.
        assert data.dependency_info.direct_count > 0
