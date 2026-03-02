"""
deps.dev API client for version graphs and dependency health data.

Provides:
- Full version history for major_versions_per_year calculation
- Transitive dependency vulnerability/deprecation data
- Reverse dependency counts
- Fallback identity resolution when registry metadata incomplete
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict, Any
from urllib.parse import quote

import httpx
from packaging.version import Version, parse as parse_version

logger = logging.getLogger(__name__)


@dataclass
class VersionInfo:
    """Version information from deps.dev."""
    version: str
    published_at: Optional[datetime] = None
    is_prerelease: bool = False
    is_yanked: bool = False


@dataclass
class DependencyInfo:
    """Dependency health information."""
    direct_count: int = 0
    transitive_count: int = 0
    vulnerable_count: int = 0
    deprecated_count: int = 0
    outdated_count: int = 0


@dataclass
class DepsDevData:
    """Complete deps.dev data for a package."""
    package_name: str
    ecosystem: str
    github_url: Optional[str] = None
    versions: List[VersionInfo] = None
    dependency_info: Optional[DependencyInfo] = None
    reverse_dep_count: int = 0
    first_release_date: Optional[datetime] = None
    latest_version: Optional[str] = None
    major_versions_per_year: Optional[float] = None
    release_cv: Optional[float] = None

    def __post_init__(self):
        if self.versions is None:
            self.versions = []


class DepsDevClient:
    """Client for deps.dev API (Google Open Source Insights)."""

    BASE_URL = "https://api.deps.dev/v3"

    def __init__(self, timeout: int = 30):
        self.client = httpx.Client(timeout=timeout)

    def get_package_data(self, package_name: str, ecosystem: str) -> Optional[DepsDevData]:
        """Get complete package data from deps.dev.

        Args:
            package_name: Package name
            ecosystem: Ecosystem (pypi, npm, cargo, go)

        Returns:
            DepsDevData with all available information
        """
        # Map our ecosystem names to deps.dev format
        ecosystem_map = {
            'pypi': 'pypi',
            'npm': 'npm',
            'cargo': 'cargo',
            'go': 'go'
        }

        deps_ecosystem = ecosystem_map.get(ecosystem.lower())
        if not deps_ecosystem:
            logger.warning(f"Unsupported ecosystem for deps.dev: {ecosystem}")
            return None

        try:
            # Get package info
            package_url = f"{self.BASE_URL}/systems/{deps_ecosystem}/packages/{quote(package_name, safe='')}"
            response = self.client.get(package_url)

            if response.status_code == 404:
                return None

            response.raise_for_status()
            package_data = response.json()

            # Extract GitHub URL
            github_url = self._extract_github_url(package_data)

            # Get version history
            versions_url = f"{package_url}/versions"
            versions_response = self.client.get(versions_url)
            versions_data = versions_response.json() if versions_response.status_code == 200 else {}

            versions = self._parse_versions(versions_data)

            # Calculate version metrics
            first_release, latest_version = None, None
            major_versions_per_year, release_cv = None, None

            if versions:
                first_release = min(v.published_at for v in versions if v.published_at)
                latest_version = self._get_latest_stable_version(versions)

                if first_release:
                    major_versions_per_year = self._calculate_major_versions_per_year(versions, first_release)
                    release_cv = self._calculate_release_cv(versions)

            # Get dependency info for latest version
            dependency_info = None
            if latest_version:
                dep_url = f"{package_url}/versions/{quote(latest_version, safe='')}"
                dep_response = self.client.get(dep_url)
                if dep_response.status_code == 200:
                    dep_data = dep_response.json()
                    dependency_info = self._parse_dependency_info(dep_data)

            # Get reverse dependency count
            reverse_dep_count = package_data.get('dependentCount', 0)

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
                release_cv=release_cv
            )

        except httpx.TimeoutException:
            logger.warning(f"Timeout fetching deps.dev data for {package_name}")
            return None
        except Exception as e:
            logger.warning(f"Error fetching deps.dev data for {package_name}: {e}")
            return None

    def _extract_github_url(self, package_data: dict) -> Optional[str]:
        """Extract GitHub URL from deps.dev package data."""
        # Check for source repository
        source_repo = package_data.get('sourceRepository', {})
        if source_repo.get('type') == 'GITHUB':
            url = source_repo.get('url', '')
            # Validate it's a proper GitHub URL
            if 'github.com' in url:
                # Clean up the URL
                url = url.replace('git+', '').replace('.git', '')
                if url.startswith('git://'):
                    url = url.replace('git://', 'https://')
                if not url.startswith('https://'):
                    url = f"https://{url}"

                # Validate format
                import re
                pattern = r'^https://github\.com/([^/]+)/([^/]+)/?.*$'
                match = re.match(pattern, url)
                if match:
                    owner, repo = match.groups()
                    return f"https://github.com/{owner}/{repo.rstrip('/')}"

        return None

    def _parse_versions(self, versions_data: dict) -> List[VersionInfo]:
        """Parse version history from deps.dev response."""
        versions = []

        for version_item in versions_data.get('versions', []):
            version_key = version_item.get('versionKey', {})
            version_str = version_key.get('version', '')

            if not version_str:
                continue

            # Parse published timestamp
            published_at = None
            published_ts = version_item.get('publishedAt')
            if published_ts:
                try:
                    # deps.dev uses RFC3339 format
                    published_at = datetime.fromisoformat(published_ts.replace('Z', '+00:00'))
                except (ValueError, TypeError):
                    pass

            # Check if prerelease
            is_prerelease = False
            try:
                v = parse_version(version_str)
                is_prerelease = v.is_prerelease
            except:
                # If parsing fails, check for common prerelease patterns
                is_prerelease = any(x in version_str.lower() for x in
                                  ['alpha', 'beta', 'rc', 'dev', 'pre'])

            versions.append(VersionInfo(
                version=version_str,
                published_at=published_at,
                is_prerelease=is_prerelease,
                is_yanked=version_item.get('isYanked', False)
            ))

        return versions

    def _get_latest_stable_version(self, versions: List[VersionInfo]) -> Optional[str]:
        """Get the latest stable (non-prerelease) version."""
        stable_versions = [
            v for v in versions
            if not v.is_prerelease and not v.is_yanked and v.published_at
        ]

        if not stable_versions:
            return None

        # Sort by published date
        stable_versions.sort(key=lambda v: v.published_at, reverse=True)
        return stable_versions[0].version

    def _calculate_major_versions_per_year(self, versions: List[VersionInfo],
                                          first_release: datetime) -> float:
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
            except:
                # Fallback: extract first number
                import re
                match = re.match(r'^v?(\d+)', v.version)
                if match:
                    major_versions.add(int(match.group(1)))

        if not major_versions:
            return 0.0

        years_active = max(1.0, (datetime.utcnow() - first_release).days / 365.0)
        return len(major_versions) / years_active

    def _calculate_release_cv(self, versions: List[VersionInfo]) -> float:
        """Calculate coefficient of variation for release intervals."""
        stable_versions = [
            v for v in versions
            if not v.is_prerelease and not v.is_yanked and v.published_at
        ]

        if len(stable_versions) < 3:
            return 0.0  # Not enough data

        # Sort by date
        stable_versions.sort(key=lambda v: v.published_at)

        # Calculate intervals between releases (in days)
        intervals = []
        for i in range(1, len(stable_versions)):
            delta = stable_versions[i].published_at - stable_versions[i-1].published_at
            intervals.append(delta.days)

        if not intervals:
            return 0.0

        # Calculate mean and std dev
        mean_interval = sum(intervals) / len(intervals)
        if mean_interval == 0:
            return 0.0

        variance = sum((x - mean_interval) ** 2 for x in intervals) / len(intervals)
        std_dev = variance ** 0.5

        return std_dev / mean_interval

    def _parse_dependency_info(self, version_data: dict) -> DependencyInfo:
        """Parse dependency health information from version data."""
        info = DependencyInfo()

        # Get dependency counts
        relations = version_data.get('relations', [])

        direct_deps = [r for r in relations if r.get('relation') == 'DIRECT']
        info.direct_count = len(direct_deps)

        # Get all transitive dependencies
        all_deps = version_data.get('resolvedDependencies', [])
        info.transitive_count = len(all_deps)

        # Count vulnerable and deprecated
        for dep in all_deps:
            advisories = dep.get('advisories', [])
            if advisories:
                info.vulnerable_count += 1

            if dep.get('isDeprecated'):
                info.deprecated_count += 1

            # Check if outdated (simplified - would need more complex logic)
            # For now, we skip this as it requires comparing versions

        return info

    def get_identity_fallback(self, package_name: str, ecosystem: str) -> Optional[str]:
        """Use deps.dev as fallback for GitHub URL resolution.

        Args:
            package_name: Package name
            ecosystem: Ecosystem (pypi, npm, cargo, go)

        Returns:
            GitHub URL if found, None otherwise
        """
        data = self.get_package_data(package_name, ecosystem)
        return data.github_url if data else None