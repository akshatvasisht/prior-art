"""
deps.dev API client for version graphs and dependency health data.

Provides:
- Full version history for major_versions_per_year calculation
- Transitive dependency vulnerability/deprecation data
- Reverse dependency counts
- Fallback identity resolution when registry metadata incomplete
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
from packaging.version import parse as parse_version

from .utils import validate_github_url

logger = logging.getLogger(__name__)


@dataclass
class VersionInfo:
    """Version information from deps.dev."""

    version: str
    published_at: datetime | None = None
    is_prerelease: bool = False
    is_yanked: bool = False


@dataclass
class DependencyInfo:
    """Dependency health information."""

    direct_count: int = 0
    vulnerable_count: int = 0
    deprecated_count: int = 0


@dataclass
class DepsDevData:
    """Complete deps.dev data for a package."""

    package_name: str
    ecosystem: str
    github_url: str | None = None
    versions: list[VersionInfo] = field(default_factory=list)
    dependency_info: DependencyInfo | None = None
    reverse_dep_count: int = 0
    first_release_date: datetime | None = None
    latest_version: str | None = None
    major_versions_per_year: float | None = None
    release_cv: float | None = None


class DepsDevClient:
    """Client for deps.dev API (Google Open Source Insights)."""

    BASE_URL = "https://api.deps.dev/v3"

    def __init__(self, timeout: int = 30):
        self.client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self.client.close()

    def __enter__(self) -> "DepsDevClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get_package_data(self, package_name: str, ecosystem: str) -> DepsDevData | None:
        """Get complete package data from deps.dev.

        Args:
            package_name: Package name
            ecosystem: Ecosystem (pypi, npm, cargo, go, maven, nuget)

        Returns:
            DepsDevData with all available information
        """
        # Map our ecosystem names to deps.dev format
        ecosystem_map = {
            "pypi": "pypi",
            "npm": "npm",
            "cargo": "cargo",
            "go": "go",
            "maven": "maven",
            "nuget": "nuget",
        }

        deps_ecosystem = ecosystem_map.get(ecosystem.lower())
        if not deps_ecosystem:
            logger.warning(f"Unsupported ecosystem for deps.dev: {ecosystem}")
            return None

        # Primary call — errors propagate so caller can distinguish service failure from missing data.
        # 404 means the package is not indexed (service is up); anything else is a service error.
        package_url = (
            f"{self.BASE_URL}/systems/{deps_ecosystem}/packages/{quote(package_name, safe='')}"
        )
        response = self.client.get(package_url)

        if response.status_code == 404:
            return None  # Package not in deps.dev; service is available

        response.raise_for_status()
        package_data = response.json()

        github_url = self._extract_github_url(package_data)
        reverse_dep_count = package_data.get("dependentCount", 0)

        # Secondary calls — optional enrichment, fail gracefully
        versions: list[VersionInfo] = []
        try:
            versions_response = self.client.get(f"{package_url}/versions")
            if versions_response.status_code == 200:
                versions = self._parse_versions(versions_response.json())
        except Exception:
            pass

        first_release, latest_version = None, None
        major_versions_per_year, release_cv = None, None

        if versions:
            dated = [v for v in versions if v.published_at]
            if dated:
                first_release = min(v.published_at for v in dated)
            latest_version = self._get_latest_stable_version(versions)

            if first_release:
                major_versions_per_year = self._calculate_major_versions_per_year(
                    versions, first_release
                )
                release_cv = self._calculate_release_cv(versions)

        dependency_info = None
        if latest_version:
            try:
                dep_url = f"{package_url}/versions/{quote(latest_version, safe='')}"
                dep_response = self.client.get(dep_url)
                if dep_response.status_code == 200:
                    dependency_info = self._parse_dependency_info(dep_response.json())
            except Exception:
                pass

        return DepsDevData(
            package_name=package_name,
            ecosystem=ecosystem,
            github_url=github_url,
            versions=versions,
            dependency_info=dependency_info,
            reverse_dep_count=reverse_dep_count,
            first_release_date=first_release,
            latest_version=latest_version,
            major_versions_per_year=major_versions_per_year,
            release_cv=release_cv,
        )

    def _extract_github_url(self, package_data: dict) -> str | None:
        """Extract GitHub URL from deps.dev package data."""
        source_repo = package_data.get("sourceRepository", {})
        if source_repo.get("type") == "GITHUB":
            url = source_repo.get("url", "")
            if "github.com" in url:
                return validate_github_url(url)
        return None

    def _parse_versions(self, versions_data: dict) -> list[VersionInfo]:
        """Parse version history from deps.dev response."""
        versions = []

        for version_item in versions_data.get("versions", []):
            version_key = version_item.get("versionKey", {})
            version_str = version_key.get("version", "")

            if not version_str:
                continue

            # Parse published timestamp
            published_at = None
            published_ts = version_item.get("publishedAt")
            if published_ts:
                try:
                    # deps.dev uses RFC3339 format
                    published_at = datetime.fromisoformat(published_ts.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass

            # Check if prerelease
            is_prerelease = False
            try:
                v = parse_version(version_str)
                is_prerelease = v.is_prerelease
            except Exception:
                # If parsing fails, check for common prerelease patterns
                is_prerelease = any(
                    x in version_str.lower() for x in ["alpha", "beta", "rc", "dev", "pre"]
                )

            versions.append(
                VersionInfo(
                    version=version_str,
                    published_at=published_at,
                    is_prerelease=is_prerelease,
                    is_yanked=version_item.get("isYanked", False),
                )
            )

        return versions

    def _get_latest_stable_version(self, versions: list[VersionInfo]) -> str | None:
        """Get the latest stable (non-prerelease) version."""
        stable_versions = [
            v for v in versions if not v.is_prerelease and not v.is_yanked and v.published_at
        ]

        if not stable_versions:
            return None

        # Sort by published date
        stable_versions.sort(key=lambda v: v.published_at, reverse=True)
        return stable_versions[0].version

    def _calculate_major_versions_per_year(
        self, versions: list[VersionInfo], first_release: datetime
    ) -> float:
        """Calculate major versions per year metric."""
        if not first_release:
            return 0.0

        major_versions = set()

        for v in versions:
            if v.is_prerelease or v.is_yanked:
                continue

            try:
                parsed = parse_version(v.version)
                major_versions.add(parsed.major)
            except Exception:
                # Fallback: extract first number
                import re

                match = re.match(r"^v?(\d+)", v.version)
                if match:
                    major_versions.add(int(match.group(1)))

        if not major_versions:
            return 0.0

        years_active = max(1.0, (datetime.now(timezone.utc) - first_release).days / 365.0)
        return len(major_versions) / years_active

    def _calculate_release_cv(self, versions: list[VersionInfo]) -> float:
        """Calculate coefficient of variation for release intervals."""
        stable_versions = [
            v for v in versions if not v.is_prerelease and not v.is_yanked and v.published_at
        ]

        if len(stable_versions) < 3:
            return 0.0  # Not enough data

        # Sort by date
        stable_versions.sort(key=lambda v: v.published_at)

        # Calculate intervals between releases (in days)
        intervals = []
        for i in range(1, len(stable_versions)):
            delta = stable_versions[i].published_at - stable_versions[i - 1].published_at
            intervals.append(delta.days)

        if not intervals:
            return 0.0  # pragma: no cover

        # Calculate mean and std dev
        mean_interval = sum(intervals) / len(intervals)
        if mean_interval == 0:
            return 0.0

        variance = sum((x - mean_interval) ** 2 for x in intervals) / len(intervals)
        std_dev = variance**0.5

        return std_dev / mean_interval

    def _parse_dependency_info(self, version_data: dict) -> DependencyInfo:
        """Parse dependency health information from version data."""
        info = DependencyInfo()

        # Get dependency counts
        relations = version_data.get("relations", [])

        direct_deps = [r for r in relations if r.get("relation") == "DIRECT"]
        info.direct_count = len(direct_deps)

        # Count vulnerable and deprecated across all resolved dependencies
        for dep in version_data.get("resolvedDependencies", []):
            if dep.get("advisories"):
                info.vulnerable_count += 1
            if dep.get("isDeprecated"):
                info.deprecated_count += 1

        return info

    def get_identity_fallback(self, package_name: str, ecosystem: str) -> str | None:
        """Use deps.dev as fallback for GitHub URL resolution.

        Args:
            package_name: Package name
            ecosystem: Ecosystem (pypi, npm, cargo, go, maven, nuget)

        Returns:
            GitHub URL if found, None otherwise
        """
        data = self.get_package_data(package_name, ecosystem)
        return data.github_url if data else None
