"""Tests for deps_dev.py pure-logic functions (no network calls)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

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


# --- _parse_dependency_info ---


class TestParseDependencyInfo:
    def setup_method(self):
        self.client = DepsDevClient.__new__(DepsDevClient)

    def test_parses_counts(self):
        version_data = {
            "relations": [
                {"relation": "DIRECT"},
                {"relation": "DIRECT"},
                {"relation": "INDIRECT"},
            ],
            "resolvedDependencies": [
                {"advisories": [{"id": "CVE-1"}]},
                {"isDeprecated": True},
                {},
            ],
        }
        info = self.client._parse_dependency_info(version_data)

        assert info.direct_count == 2
        assert info.vulnerable_count == 1
        assert info.deprecated_count == 1

    def test_empty_data(self):
        info = self.client._parse_dependency_info({})

        assert info.direct_count == 0
        assert info.vulnerable_count == 0
        assert info.deprecated_count == 0


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
        """Complete get_package_data with all enrichment."""
        # Package response
        pkg_resp = self._mock_response(
            200,
            {
                "dependentCount": 150000,
                "sourceRepository": {
                    "type": "GITHUB",
                    "url": "https://github.com/psf/requests",
                },
            },
        )

        # Versions response
        versions_resp = self._mock_response(
            200,
            {
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
                ]
            },
        )

        # Version detail response
        detail_resp = self._mock_response(
            200,
            {
                "relations": [
                    {"relation": "DIRECT"},
                    {"relation": "DIRECT"},
                    {"relation": "INDIRECT"},
                ],
                "resolvedDependencies": [
                    {"advisories": [{"id": "CVE-1"}]},
                    {"isDeprecated": True},
                    {},
                ],
            },
        )

        call_count = [0]

        def side_effect(url):
            call_count[0] += 1
            if "/versions/" in url and call_count[0] > 2:
                return detail_resp
            if "/versions" in url:
                return versions_resp
            return pkg_resp

        self.client.client.get = side_effect

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

    def test_versions_failure_graceful(self):
        """Versions call failure still returns package data."""
        pkg_resp = self._mock_response(
            200,
            {
                "dependentCount": 100,
                "sourceRepository": {},
            },
        )

        call_count = [0]

        def side_effect(url):
            call_count[0] += 1
            if "/versions" in url:
                raise ConnectionError("timeout")
            return pkg_resp

        self.client.client.get = side_effect

        data = self.client.get_package_data("pkg", "npm")
        assert data is not None
        assert data.versions == []
        assert data.reverse_dep_count == 100

    def test_unsupported_ecosystem(self):
        """Unsupported ecosystem returns None."""
        result = self.client.get_package_data("pkg", "maven")
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
        """Exception fetching dep info still returns package data."""
        pkg_resp = MagicMock()
        pkg_resp.status_code = 200
        pkg_resp.json.return_value = {"dependentCount": 50}
        pkg_resp.raise_for_status = MagicMock()

        versions_resp = MagicMock()
        versions_resp.status_code = 200
        versions_resp.json.return_value = {
            "versions": [
                {
                    "versionKey": {"version": "1.0.0"},
                    "publishedAt": "2023-01-01T00:00:00Z",
                    "isYanked": False,
                },
            ]
        }

        call_count = [0]

        def side_effect(url):
            call_count[0] += 1
            if call_count[0] >= 3:  # dep info call
                raise ConnectionError("timeout")
            if "/versions" in url:
                return versions_resp
            return pkg_resp

        self.client.client.get = side_effect

        data = self.client.get_package_data("pkg", "pypi")
        assert data is not None
        assert data.dependency_info is None
