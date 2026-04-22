"""Tests for registry clients.

Note: Integration tests make real API calls; unit tests mock httpx.
"""

from unittest.mock import MagicMock

import pytest

from priorart.core.registry import (
    CratesIOClient,
    MavenCentralClient,
    NPMClient,
    NuGetClient,
    PackageCandidate,
    PkgGoDevClient,
    PyPIClient,
    RegistryClient,
    get_registry_client,
)
from priorart.core.utils import validate_github_url


@pytest.mark.integration
def test_pypi_get_package_info():
    """Test fetching package info from PyPI."""
    client = PyPIClient()

    result = client.get_package_info("requests")

    assert result is not None
    assert result.name == "requests"
    assert result.registry == "pypi"
    assert result.description is not None
    assert result.version is not None


@pytest.mark.integration
def test_pypi_package_not_found():
    """Test PyPI returns None for non-existent package."""
    client = PyPIClient()

    result = client.get_package_info("this-package-definitely-does-not-exist-12345")

    assert result is None


@pytest.mark.integration
def test_npm_search():
    """Test searching npm registry."""
    client = NPMClient()

    results = client.search("express", max_results=5)

    assert len(results) > 0
    assert any(r.name == "express" for r in results)
    # All results should be npm
    assert all(r.registry == "npm" for r in results)


@pytest.mark.integration
def test_npm_get_package_info():
    """Test fetching package info from npm."""
    client = NPMClient()

    result = client.get_package_info("express")

    assert result is not None
    assert result.name == "express"
    assert result.registry == "npm"
    assert result.description is not None


@pytest.mark.integration
def test_crates_io_search():
    """Test searching crates.io."""
    client = CratesIOClient()

    results = client.search("serde", max_results=5)

    assert len(results) > 0
    # All results should be cargo
    assert all(r.registry == "cargo" for r in results)


@pytest.mark.integration
def test_crates_io_get_package_info():
    """Test fetching crate info from crates.io."""
    client = CratesIOClient()

    result = client.get_package_info("serde")

    assert result is not None
    assert result.name == "serde"
    assert result.registry == "cargo"


def test_validate_github_url():
    """Test GitHub URL validation."""
    # Valid URLs
    assert validate_github_url("https://github.com/psf/requests") is not None
    assert validate_github_url("https://github.com/owner/repo/") is not None

    # Invalid URLs
    assert validate_github_url("http://github.com/owner/repo") is None
    assert validate_github_url("https://gitlab.com/owner/repo") is None
    assert validate_github_url("not-a-url") is None
    assert validate_github_url("") is None


def test_validate_github_url_strips_git_suffix():
    """Test that .git suffix is removed."""
    result = validate_github_url("https://github.com/owner/repo.git")

    assert result == "https://github.com/owner/repo"


def test_get_registry_client():
    """Test getting correct registry client for language."""
    assert isinstance(get_registry_client("python"), PyPIClient)
    assert isinstance(get_registry_client("javascript"), NPMClient)
    assert isinstance(get_registry_client("typescript"), NPMClient)
    assert isinstance(get_registry_client("rust"), CratesIOClient)
    assert isinstance(get_registry_client("cargo"), CratesIOClient)
    assert isinstance(get_registry_client("java"), MavenCentralClient)
    assert isinstance(get_registry_client("kotlin"), MavenCentralClient)
    assert isinstance(get_registry_client("scala"), MavenCentralClient)
    assert isinstance(get_registry_client("csharp"), NuGetClient)
    assert isinstance(get_registry_client("dotnet"), NuGetClient)
    assert isinstance(get_registry_client("fsharp"), NuGetClient)


def test_get_registry_client_unknown_language():
    """Test that unknown language raises error."""
    with pytest.raises(ValueError, match="Unsupported language"):
        get_registry_client("cobol")


def test_package_candidate_structure():
    """Test PackageCandidate dataclass structure."""
    candidate = PackageCandidate(
        name="test-package",
        registry="pypi",
        description="A test package",
        version="1.0.0",
        github_url="https://github.com/test/package",
    )

    assert candidate.name == "test-package"
    assert candidate.registry == "pypi"
    assert candidate.maintainers == []  # Default empty list


@pytest.mark.integration
def test_npm_extract_github_url():
    """Test extracting GitHub URL from npm package."""
    client = NPMClient()

    result = client.get_package_info("express")

    assert result is not None, "npm API returned None for 'express'"
    assert result.github_url is not None, "express package should have a GitHub URL"
    assert "github.com" in result.github_url
    assert result.github_url.startswith("https://")


@pytest.mark.integration
def test_pypi_extract_maintainers():
    """Test extracting maintainers from PyPI."""
    client = PyPIClient()

    result = client.get_package_info("requests")

    assert result is not None, "PyPI API returned None for 'requests'"
    assert result.author or result.maintainers, "requests package should have author or maintainers"


# --- Unit tests (no network) ---


def test_package_candidate_eq_and_hash():
    """Test __eq__ and __hash__ on PackageCandidate."""
    a = PackageCandidate(name="pkg", registry="pypi", description="A")
    b = PackageCandidate(name="pkg", registry="pypi", description="B")
    c = PackageCandidate(name="other", registry="pypi")

    # Same name+registry → equal
    assert a == b
    assert hash(a) == hash(b)

    # Different name → not equal
    assert a != c

    # Non-PackageCandidate → NotImplemented
    assert a.__eq__("string") is NotImplemented


def test_registry_client_context_manager():
    """RegistryClient supports with-statement."""
    with RegistryClient() as client:
        assert client is not None


def test_registry_client_search_not_implemented():
    """Base RegistryClient.search raises NotImplementedError."""
    client = RegistryClient()
    with pytest.raises(NotImplementedError):
        client.search("test")


def test_pypi_search_mocked():
    """PyPI search attempts exact match and keyword variants."""
    client = PyPIClient()

    def mock_get(url, **kwargs):
        resp = MagicMock()
        if "pypistats.org" in url:
            resp.status_code = 200
            resp.json.return_value = {"data": {"last_week": 100000}}
            return resp
        if "/pypi/" in url:
            # Return data for any package lookup
            pkg_name = url.split("/pypi/")[1].split("/")[0]
            if pkg_name in ("http-client", "http", "python-http", "py-http"):
                resp.status_code = 200
                resp.json.return_value = {
                    "info": {
                        "summary": f"Package {pkg_name}",
                        "version": "1.0.0",
                        "project_urls": {"Source": "https://github.com/owner/repo"},
                        "home_page": None,
                        "author": "Author",
                        "author_email": None,
                        "maintainer": None,
                        "maintainer_email": None,
                        "license": "MIT",
                    }
                }
                return resp
        resp.status_code = 404
        return resp

    client.client = MagicMock()
    client.client.get = mock_get

    results = client.search("http client", max_results=10)

    assert len(results) >= 1
    assert all(r.registry == "pypi" for r in results)


def test_pypi_homepage_github_fallback():
    """PyPI falls back to home_page for GitHub URL."""
    client = PyPIClient()
    client.client = MagicMock()

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "info": {
            "summary": "A lib",
            "version": "1.0.0",
            "project_urls": {"Documentation": "https://docs.example.com"},
            "home_page": "https://github.com/owner/repo",
            "author": "Author",
            "author_email": None,
            "maintainer": None,
            "maintainer_email": None,
            "license": "MIT",
        }
    }

    stats_resp = MagicMock()
    stats_resp.status_code = 404

    def side_effect(url, **kwargs):
        if "pypistats.org" in url:
            return stats_resp
        return resp

    client.client.get = side_effect

    result = client.get_package_info("test-pkg")

    assert result is not None
    assert result.github_url == "https://github.com/owner/repo"


def test_pypi_maintainer_email_extraction():
    """PyPI extracts names from author_email and maintainer_email."""
    client = PyPIClient()
    client.client = MagicMock()

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "info": {
            "summary": "A lib",
            "version": "1.0.0",
            "project_urls": None,
            "home_page": None,
            "author": None,
            "author_email": "Alice <alice@example.com>",
            "maintainer": None,
            "maintainer_email": "Bob <bob@example.com>",
            "license": "MIT",
        }
    }

    stats_resp = MagicMock()
    stats_resp.status_code = 404

    def side_effect(url, **kwargs):
        if "pypistats.org" in url:
            return stats_resp
        return resp

    client.client.get = side_effect

    result = client.get_package_info("email-pkg")

    assert result is not None
    assert "Alice" in result.maintainers
    assert "Bob" in result.maintainers


def test_go_client_search_and_info():
    """PkgGoDevClient returns candidates for known queries."""
    client = PkgGoDevClient()

    results = client.search("http client", max_results=5)
    assert len(results) > 0
    assert any("resty" in r.name for r in results)

    info = client.get_package_info("github.com/go-resty/resty")
    assert info is not None
    assert info.github_url is not None
    assert "github.com" in info.github_url


def test_npm_extract_github_url_git_prefix():
    """NPM _extract_github_url handles git+ prefix."""
    client = NPMClient()

    pkg_data = {"repository": {"url": "git+https://github.com/owner/repo.git"}}
    url = client._extract_github_url(pkg_data)
    assert url == "https://github.com/owner/repo"


def test_npm_search_mocked():
    """npm search parses npms.io response correctly."""
    client = NPMClient()
    client.client = MagicMock()

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "results": [
            {
                "package": {
                    "name": "express",
                    "description": "Fast web framework",
                    "version": "4.18.0",
                    "links": {"repository": "https://github.com/expressjs/express"},
                    "maintainers": [{"username": "dougwilson"}],
                }
            }
        ]
    }
    resp.raise_for_status = MagicMock()

    client.client.get = MagicMock(return_value=resp)

    results = client.search("express", max_results=5)
    assert len(results) == 1
    assert results[0].name == "express"
    assert results[0].github_url == "https://github.com/expressjs/express"
    assert "dougwilson" in results[0].maintainers


def test_crates_io_search_mocked():
    """crates.io search parses response correctly."""
    client = CratesIOClient()
    client.client = MagicMock()

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "crates": [
            {
                "name": "serde",
                "description": "Serialization framework",
                "newest_version": "1.0.200",
                "repository": "https://github.com/serde-rs/serde",
                "homepage": None,
                "recent_downloads": 5000000,
            }
        ]
    }
    resp.raise_for_status = MagicMock()

    client.client.get = MagicMock(return_value=resp)

    results = client.search("serde", max_results=5)
    assert len(results) == 1
    assert results[0].name == "serde"
    assert results[0].github_url == "https://github.com/serde-rs/serde"


def test_crates_io_get_package_info_mocked():
    """crates.io get_package_info parses crate data."""
    client = CratesIOClient()
    client.client = MagicMock()

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "crate": {
            "name": "tokio",
            "description": "Async runtime",
            "newest_version": "1.37.0",
            "repository": "https://github.com/tokio-rs/tokio",
            "homepage": "https://tokio.rs",
            "recent_downloads": 8000000,
        }
    }
    resp.raise_for_status = MagicMock()

    client.client.get = MagicMock(return_value=resp)

    result = client.get_package_info("tokio")
    assert result is not None
    assert result.name == "tokio"
    assert result.weekly_downloads == 8000000


def test_npm_get_package_info_mocked():
    """npm get_package_info parses registry response."""
    client = NPMClient()
    client.client = MagicMock()

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "name": "lodash",
        "description": "Utility library",
        "dist-tags": {"latest": "4.17.21"},
        "versions": {
            "4.17.21": {
                "homepage": "https://lodash.com",
                "license": "MIT",
                "repository": {"url": "git+https://github.com/lodash/lodash.git"},
            }
        },
        "maintainers": [{"name": "jdalton"}],
    }
    resp.raise_for_status = MagicMock()

    client.client.get = MagicMock(return_value=resp)

    result = client.get_package_info("lodash")
    assert result is not None
    assert result.name == "lodash"
    assert result.version == "4.17.21"
    assert result.github_url == "https://github.com/lodash/lodash"
    assert "jdalton" in result.maintainers


def test_registry_get_package_info_not_implemented():
    """Base RegistryClient.get_package_info raises NotImplementedError."""
    client = RegistryClient()
    with pytest.raises(NotImplementedError):
        client.get_package_info("test")


def test_pypi_maintainer_field():
    """PyPI includes maintainer when distinct from author."""
    client = PyPIClient()
    client.client = MagicMock()

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "info": {
            "summary": "lib",
            "version": "1.0.0",
            "project_urls": None,
            "home_page": None,
            "author": "Alice",
            "author_email": None,
            "maintainer": "Bob",
            "maintainer_email": None,
            "license": "MIT",
        }
    }
    stats = MagicMock()
    stats.status_code = 404

    def side_effect(url, **kw):
        return stats if "pypistats.org" in url else resp

    client.client.get = side_effect

    result = client.get_package_info("pkg")
    assert "Alice" in result.maintainers
    assert "Bob" in result.maintainers


def test_pypi_pypistats_exception():
    """PyPI handles pypistats.org exception gracefully."""
    client = PyPIClient()
    client.client = MagicMock()

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "info": {
            "summary": "lib",
            "version": "1.0.0",
            "project_urls": None,
            "home_page": None,
            "author": "A",
            "author_email": None,
            "maintainer": None,
            "maintainer_email": None,
            "license": "MIT",
        }
    }

    import httpx

    def side_effect(url, **kw):
        if "pypistats.org" in url:
            raise httpx.ConnectError("timeout")
        return resp

    client.client.get = side_effect

    result = client.get_package_info("pkg")
    assert result is not None
    assert result.weekly_downloads is None


def test_pypi_get_info_exception():
    """PyPI get_package_info returns None on general exception."""
    client = PyPIClient()
    client.client = MagicMock()
    client.client.get.side_effect = RuntimeError("network error")

    result = client.get_package_info("pkg")
    assert result is None


def test_npm_search_exception():
    """npm search returns empty list on exception."""
    client = NPMClient()
    client.client = MagicMock()
    client.client.get.side_effect = RuntimeError("network error")

    results = client.search("test")
    assert results == []


def test_npm_get_info_404():
    """npm get_package_info returns None for 404."""
    client = NPMClient()
    client.client = MagicMock()

    resp = MagicMock()
    resp.status_code = 404
    client.client.get.return_value = resp

    result = client.get_package_info("nonexistent")
    assert result is None


def test_npm_get_info_exception():
    """npm get_package_info returns None on exception."""
    client = NPMClient()
    client.client = MagicMock()
    client.client.get.side_effect = RuntimeError("network error")

    result = client.get_package_info("pkg")
    assert result is None


def test_npm_extract_github_url_string_repo():
    """NPM _extract_github_url handles string repo field."""
    client = NPMClient()
    result = client._extract_github_url({"repository": "https://github.com/owner/repo"})
    assert result == "https://github.com/owner/repo"


def test_npm_extract_github_url_git_protocol():
    """NPM _extract_github_url converts git:// to https://."""
    client = NPMClient()
    result = client._extract_github_url({"repository": {"url": "git://github.com/owner/repo.git"}})
    assert result == "https://github.com/owner/repo"


def test_npm_extract_github_url_no_match():
    """NPM _extract_github_url returns None when nothing matches."""
    client = NPMClient()
    result = client._extract_github_url({"repository": {"url": "https://gitlab.com/owner/repo"}})
    assert result is None


def test_crates_search_exception():
    """crates.io search returns empty list on exception."""
    client = CratesIOClient()
    client.client = MagicMock()
    client.client.get.side_effect = RuntimeError("network error")

    results = client.search("test")
    assert results == []


def test_crates_get_info_404():
    """crates.io get_package_info returns None for 404."""
    client = CratesIOClient()
    client.client = MagicMock()

    resp = MagicMock()
    resp.status_code = 404
    client.client.get.return_value = resp

    result = client.get_package_info("nonexistent")
    assert result is None


def test_crates_get_info_exception():
    """crates.io get_package_info returns None on exception."""
    client = CratesIOClient()
    client.client = MagicMock()
    client.client.get.side_effect = RuntimeError("network error")

    result = client.get_package_info("pkg")
    assert result is None


@pytest.mark.integration
def test_maven_central_search():
    """Maven Central search returns groupId:artifactId candidates."""
    client = MavenCentralClient()
    results = client.search("junit", max_results=5)
    assert len(results) > 0
    assert all(r.registry == "maven" for r in results)
    assert all(":" in r.name for r in results)


@pytest.mark.integration
def test_maven_central_get_package_info():
    """Maven Central get_package_info via ecosyste.ms returns full fields."""
    client = MavenCentralClient()
    result = client.get_package_info("junit:junit")
    assert result is not None
    assert result.name == "junit:junit"
    assert result.registry == "maven"
    assert result.description is not None
    assert "unit testing" in result.description.lower()


def test_maven_search_handles_failure():
    """Maven Central search returns empty list on HTTP error."""
    client = MavenCentralClient()
    client.client = MagicMock()
    resp = MagicMock()
    resp.status_code = 500
    client.client.get.return_value = resp
    assert client.search("anything") == []


def test_maven_get_info_not_found():
    """Maven Central get_package_info returns None on 404."""
    client = MavenCentralClient()
    client.client = MagicMock()
    resp = MagicMock()
    resp.status_code = 404
    client.client.get.return_value = resp
    assert client.get_package_info("nope:nope") is None


@pytest.mark.integration
def test_nuget_search():
    """NuGet Azure search returns packages with descriptions."""
    client = NuGetClient()
    results = client.search("json", max_results=5)
    assert len(results) > 0
    assert all(r.registry == "nuget" for r in results)
    assert any(r.description for r in results)


@pytest.mark.integration
def test_nuget_get_package_info():
    """NuGet get_package_info via ecosyste.ms returns full fields."""
    client = NuGetClient()
    result = client.get_package_info("newtonsoft.json")
    assert result is not None
    assert result.name.lower() == "newtonsoft.json"
    assert result.registry == "nuget"
    assert result.description is not None


def test_nuget_search_handles_failure():
    """NuGet search returns empty list on HTTP error."""
    client = NuGetClient()
    client.client = MagicMock()
    resp = MagicMock()
    resp.status_code = 500
    client.client.get.return_value = resp
    assert client.search("anything") == []


def test_nuget_get_info_not_found():
    """NuGet get_package_info returns None on 404."""
    client = NuGetClient()
    client.client = MagicMock()
    resp = MagicMock()
    resp.status_code = 404
    client.client.get.return_value = resp
    assert client.get_package_info("nonexistent") is None


def test_maven_search_parses_solr_response():
    """Maven Central search extracts groupId:artifactId from Solr docs."""
    client = MavenCentralClient()
    client.client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "response": {
            "docs": [
                {"g": "junit", "a": "junit", "latestVersion": "4.13.2"},
                {"g": "org.slf4j", "a": "slf4j-api", "latestVersion": "2.0.9"},
                # Missing group — should be skipped.
                {"a": "orphan", "latestVersion": "1.0.0"},
            ]
        }
    }
    client.client.get.return_value = resp

    results = client.search("junit", max_results=5)
    assert len(results) == 2
    assert results[0].name == "junit:junit"
    assert results[0].version == "4.13.2"
    assert results[0].registry == "maven"
    assert results[1].name == "org.slf4j:slf4j-api"


def test_maven_search_network_error_returns_empty():
    """Maven Central search returns empty list on RequestError."""
    import httpx

    client = MavenCentralClient()
    client.client = MagicMock()
    client.client.get.side_effect = httpx.RequestError("boom")
    assert client.search("x") == []


def test_maven_get_info_parses_ecosystems_response():
    """Maven Central get_package_info maps ecosyste.ms fields onto PackageCandidate."""
    client = MavenCentralClient()
    client.client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "name": "junit:junit",
        "description": "JUnit is a unit testing framework.",
        "latest_release_number": "4.13.2",
        "repository_url": "https://github.com/junit-team/junit4",
        "homepage": "http://junit.org",
        "licenses": "Eclipse Public License 1.0",
    }
    client.client.get.return_value = resp

    result = client.get_package_info("junit:junit")
    assert result is not None
    assert result.name == "junit:junit"
    assert result.registry == "maven"
    assert result.description == "JUnit is a unit testing framework."
    assert result.version == "4.13.2"
    assert result.github_url == "https://github.com/junit-team/junit4"
    assert result.homepage == "http://junit.org"


def test_maven_get_info_network_error_returns_none():
    """Maven Central get_package_info returns None on RequestError."""
    import httpx

    client = MavenCentralClient()
    client.client = MagicMock()
    client.client.get.side_effect = httpx.RequestError("boom")
    assert client.get_package_info("anything:anything") is None


def test_nuget_search_parses_azure_response():
    """NuGet search extracts id/version/description from Azure Search results."""
    client = NuGetClient()
    client.client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "data": [
            {
                "id": "Newtonsoft.Json",
                "version": "13.0.4",
                "description": "Json.NET is a popular high-performance JSON framework",
                "projectUrl": "https://github.com/JamesNK/Newtonsoft.Json",
            },
            # Missing id — should be skipped.
            {"version": "1.0.0", "description": "no id"},
        ]
    }
    client.client.get.return_value = resp

    results = client.search("json", max_results=5)
    assert len(results) == 1
    assert results[0].name == "Newtonsoft.Json"
    assert results[0].version == "13.0.4"
    assert results[0].registry == "nuget"
    assert results[0].github_url == "https://github.com/JamesNK/Newtonsoft.Json"


def test_nuget_search_network_error_returns_empty():
    """NuGet search returns empty list on RequestError."""
    import httpx

    client = NuGetClient()
    client.client = MagicMock()
    client.client.get.side_effect = httpx.RequestError("boom")
    assert client.search("x") == []


def test_nuget_get_info_parses_ecosystems_response():
    """NuGet get_package_info maps ecosyste.ms fields onto PackageCandidate."""
    client = NuGetClient()
    client.client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "name": "Newtonsoft.Json",
        "description": "Json.NET is a popular high-performance JSON framework for .NET",
        "latest_release_number": "13.0.4",
        "repository_url": "https://github.com/JamesNK/Newtonsoft.Json",
        "homepage": "https://www.newtonsoft.com/json",
        "licenses": "MIT",
    }
    client.client.get.return_value = resp

    result = client.get_package_info("Newtonsoft.Json")
    assert result is not None
    assert result.name == "Newtonsoft.Json"
    assert result.registry == "nuget"
    assert result.description.startswith("Json.NET")
    assert result.version == "13.0.4"
    assert result.github_url == "https://github.com/JamesNK/Newtonsoft.Json"


def test_nuget_get_info_network_error_returns_none():
    """NuGet get_package_info returns None on RequestError."""
    import httpx

    client = NuGetClient()
    client.client = MagicMock()
    client.client.get.side_effect = httpx.RequestError("boom")
    assert client.get_package_info("anything") is None
