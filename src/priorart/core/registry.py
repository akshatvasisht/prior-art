"""
Registry API clients for package discovery.

Registry-first approach to avoid GitHub rate limits.
All registries return results ranked by download count natively.
"""

import logging
from dataclasses import dataclass, field

import httpx

from .utils import validate_github_url

logger = logging.getLogger(__name__)


@dataclass
class PackageCandidate:
    """A package candidate from registry search."""

    name: str  # Package name on registry
    registry: str  # pypi, npm, cargo, go
    description: str | None = None
    version: str | None = None
    weekly_downloads: int | None = None
    github_url: str | None = None
    homepage: str | None = None
    license: str | None = None
    author: str | None = None
    maintainers: list[str] = field(default_factory=list)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PackageCandidate):
            return NotImplemented
        return self.name == other.name and self.registry == other.registry

    def __hash__(self) -> int:
        return hash((self.name, self.registry))


class RegistryClient:
    """Base class for registry API clients."""

    def __init__(self, timeout: int = 30):
        self.client = httpx.Client(timeout=timeout)
        self.timeout = timeout

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self.client.close()

    def __enter__(self) -> "RegistryClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def search(self, query: str, max_results: int = 20) -> list[PackageCandidate]:
        """Search for packages matching query."""
        raise NotImplementedError

    def get_package_info(self, package_name: str) -> PackageCandidate | None:
        """Get detailed info for a specific package."""
        raise NotImplementedError


class PyPIClient(RegistryClient):
    """PyPI registry API client."""

    BASE_URL = "https://pypi.org"

    def search(self, query: str, max_results: int = 20) -> list[PackageCandidate]:
        """Search PyPI for packages.

        Note: PyPI's XML-RPC search API was deprecated. Using package listing
        and filtering client-side as fallback.
        """
        candidates = []

        # Try searching for exact package name first
        exact_match = self.get_package_info(query.replace(" ", "-"))
        if exact_match:
            candidates.append(exact_match)

        # Search for related packages using PyPI JSON API
        # Split query into keywords and search for packages containing them
        keywords = query.lower().split()

        # Common package prefixes for the keywords
        for keyword in keywords[:2]:  # Limit to avoid too many requests
            for prefix in ["", "python-", "py-"]:
                package_name = f"{prefix}{keyword}"
                info = self.get_package_info(package_name)
                if info and info not in candidates:
                    candidates.append(info)

        # Sort by weekly downloads if available
        candidates.sort(key=lambda x: x.weekly_downloads or 0, reverse=True)

        return candidates[:max_results]

    def get_package_info(self, package_name: str) -> PackageCandidate | None:
        """Get package info from PyPI JSON API."""
        try:
            url = f"{self.BASE_URL}/pypi/{package_name}/json"
            response = self.client.get(url)

            if response.status_code == 404:
                return None

            response.raise_for_status()
            data = response.json()

            info = data.get("info", {})

            # Extract GitHub URL from project URLs
            github_url = None
            project_urls = info.get("project_urls") or {}

            for key in ["Source", "Repository", "Source Code", "Code"]:
                url = project_urls.get(key)
                if url:
                    validated = validate_github_url(url)
                    if validated:
                        github_url = validated
                        break

            # If not found in project_urls, check home_page
            if not github_url and info.get("home_page"):
                github_url = validate_github_url(info["home_page"])

            # Extract maintainers — check both name and email fields since
            # modern PyPI metadata often uses only *_email (PEP 621 migration)
            maintainers = []
            author = info.get("author")
            if author:
                maintainers.append(author)
            elif info.get("author_email"):
                # "Name <email>" format — extract name
                email_str = info["author_email"]
                name_part = email_str.split("<")[0].strip().rstrip(",")
                if name_part:
                    maintainers.append(name_part)

            maintainer = info.get("maintainer")
            if maintainer and maintainer != author:
                maintainers.append(maintainer)
            elif not maintainer and info.get("maintainer_email"):
                email_str = info["maintainer_email"]
                name_part = email_str.split("<")[0].strip().rstrip(",")
                if name_part and name_part not in maintainers:
                    maintainers.append(name_part)

            # Fetch weekly download count from pypistats.org (unauthenticated, public)
            weekly_downloads = None
            try:
                stats_response = self.client.get(
                    f"https://pypistats.org/api/packages/{package_name}/recent"
                )
                if stats_response.status_code == 200:
                    weekly_downloads = stats_response.json().get("data", {}).get("last_week")
            except Exception:
                pass

            return PackageCandidate(
                name=package_name,
                registry="pypi",
                description=info.get("summary"),
                version=info.get("version"),
                github_url=github_url,
                homepage=info.get("home_page"),
                license=info.get("license"),
                author=info.get("author"),
                maintainers=maintainers,
                weekly_downloads=weekly_downloads,
            )

        except Exception as e:
            logger.warning(f"Failed to get PyPI package info for {package_name}: {e}")
            return None


class NPMClient(RegistryClient):
    """npm registry API client."""

    BASE_URL = "https://registry.npmjs.org"
    SEARCH_URL = "https://api.npms.io/v2/search"

    def search(self, query: str, max_results: int = 20) -> list[PackageCandidate]:
        """Search npm for packages using npms.io API."""
        try:
            params = {"q": query, "size": max_results}

            response = self.client.get(self.SEARCH_URL, params=params)
            response.raise_for_status()

            data = response.json()
            candidates = []

            for result in data.get("results", []):
                package = result.get("package", {})
                github_url = self._extract_github_url(package)

                # Extract maintainers
                maintainers = [
                    m.get("username") for m in package.get("maintainers", []) if m.get("username")
                ]

                candidates.append(
                    PackageCandidate(
                        name=package.get("name"),
                        registry="npm",
                        description=package.get("description"),
                        version=package.get("version"),
                        github_url=github_url,
                        homepage=package.get("links", {}).get("homepage"),
                        maintainers=maintainers,
                    )
                )

            return candidates

        except Exception as e:
            logger.warning(f"npm search failed for '{query}': {e}")
            return []

    def get_package_info(self, package_name: str) -> PackageCandidate | None:
        """Get detailed package info from npm registry."""
        try:
            url = f"{self.BASE_URL}/{package_name}"
            response = self.client.get(url)

            if response.status_code == 404:
                return None

            response.raise_for_status()
            data = response.json()

            latest_version = data.get("dist-tags", {}).get("latest")
            version_data = data.get("versions", {}).get(latest_version, {})

            github_url = self._extract_github_url(version_data)

            # Extract maintainers
            maintainers = [m.get("name") for m in data.get("maintainers", []) if m.get("name")]

            return PackageCandidate(
                name=package_name,
                registry="npm",
                description=data.get("description"),
                version=latest_version,
                github_url=github_url,
                homepage=version_data.get("homepage"),
                license=version_data.get("license"),
                maintainers=maintainers,
            )

        except Exception as e:
            logger.warning(f"Failed to get npm package info for {package_name}: {e}")
            return None

    def _extract_github_url(self, package_data: dict) -> str | None:
        """Extract GitHub URL from package data."""
        # Try repository field
        repo = package_data.get("repository")
        if isinstance(repo, dict):
            url = repo.get("url", "")
        elif isinstance(repo, str):
            url = repo
        else:
            url = ""

        # Clean up git URLs
        if url.startswith("git+"):
            url = url[4:]
        if url.startswith("git://"):
            url = url.replace("git://", "https://")
        if url.endswith(".git"):
            url = url[:-4]

        validated = validate_github_url(url)
        if validated:
            return validated

        # Try links field
        links = package_data.get("links", {})
        for field in ["repository", "repo", "github"]:
            if field in links:
                validated = validate_github_url(links[field])
                if validated:
                    return validated

        return None


class CratesIOClient(RegistryClient):
    """crates.io registry API client."""

    BASE_URL = "https://crates.io/api/v1"

    def search(self, query: str, max_results: int = 20) -> list[PackageCandidate]:
        """Search crates.io for packages."""
        try:
            params = {"q": query, "per_page": max_results}

            response = self.client.get(f"{self.BASE_URL}/crates", params=params)
            response.raise_for_status()

            data = response.json()
            candidates = []

            for crate in data.get("crates", []):
                github_url = None
                if crate.get("repository"):
                    github_url = validate_github_url(crate["repository"])

                candidates.append(
                    PackageCandidate(
                        name=crate.get("name"),
                        registry="cargo",
                        description=crate.get("description"),
                        version=crate.get("newest_version"),
                        github_url=github_url,
                        homepage=crate.get("homepage"),
                        weekly_downloads=crate.get("recent_downloads"),  # 90-day downloads
                    )
                )

            return candidates

        except Exception as e:
            logger.warning(f"crates.io search failed for '{query}': {e}")
            return []

    def get_package_info(self, package_name: str) -> PackageCandidate | None:
        """Get detailed crate info."""
        try:
            url = f"{self.BASE_URL}/crates/{package_name}"
            response = self.client.get(url)

            if response.status_code == 404:
                return None

            response.raise_for_status()
            data = response.json()

            crate = data.get("crate", {})
            github_url = None

            if crate.get("repository"):
                github_url = validate_github_url(crate["repository"])

            return PackageCandidate(
                name=package_name,
                registry="cargo",
                description=crate.get("description"),
                version=crate.get("newest_version"),
                github_url=github_url,
                homepage=crate.get("homepage"),
                weekly_downloads=crate.get("recent_downloads"),
            )

        except Exception as e:
            logger.warning(f"Failed to get crate info for {package_name}: {e}")
            return None


class PkgGoDevClient(RegistryClient):
    """pkg.go.dev registry client."""

    BASE_URL = "https://pkg.go.dev"

    def search(self, query: str, max_results: int = 20) -> list[PackageCandidate]:
        """Search pkg.go.dev for packages.

        Note: pkg.go.dev doesn't have a public API. Using web scraping
        as fallback or returning common known packages.
        """
        # For Go, GitHub URLs are often encoded in the module path itself
        candidates = []

        # Common Go packages based on query keywords
        query_lower = query.lower()

        # Map common queries to known packages
        known_packages = {
            "http client": ["github.com/go-resty/resty", "github.com/levigross/grequests"],
            "database": ["github.com/jmoiron/sqlx", "gorm.io/gorm"],
            "logging": ["github.com/sirupsen/logrus", "go.uber.org/zap"],
            "cli": ["github.com/spf13/cobra", "github.com/urfave/cli"],
            "testing": ["github.com/stretchr/testify", "github.com/onsi/ginkgo"],
        }

        for key, packages in known_packages.items():
            if key in query_lower:
                for pkg in packages[: max_results // 2]:
                    candidates.append(self._parse_go_package(pkg))

        return candidates[:max_results]

    def get_package_info(self, package_name: str) -> PackageCandidate | None:
        """Get Go package info."""
        # For Go packages, the name often is the GitHub URL
        github_url = None

        if package_name.startswith("github.com/"):
            parts = package_name.split("/")
            if len(parts) >= 3:
                github_url = f"https://github.com/{parts[1]}/{parts[2]}"

        return PackageCandidate(name=package_name, registry="go", github_url=github_url)

    def _parse_go_package(self, import_path: str) -> PackageCandidate:
        """Parse a Go import path into a PackageCandidate."""
        github_url = None

        if import_path.startswith("github.com/"):
            parts = import_path.split("/")
            if len(parts) >= 3:
                github_url = f"https://github.com/{parts[1]}/{parts[2]}"

        return PackageCandidate(name=import_path, registry="go", github_url=github_url)


def get_registry_client(language: str) -> RegistryClient:
    """Get the appropriate registry client for a language.

    Args:
        language: Programming language (python, javascript, typescript, go, rust)

    Returns:
        Registry client instance
    """
    language = language.lower()

    registry_map = {
        "python": PyPIClient,
        "javascript": NPMClient,
        "typescript": NPMClient,
        "node": NPMClient,
        "rust": CratesIOClient,
        "cargo": CratesIOClient,
        "go": PkgGoDevClient,
        "golang": PkgGoDevClient,
    }

    client_class = registry_map.get(language)
    if not client_class:
        raise ValueError(f"Unsupported language: {language}")

    return client_class()
