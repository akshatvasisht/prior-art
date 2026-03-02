"""Tests for registry clients.

Note: These tests make real API calls. Mark as integration tests.
"""

import pytest

from priorart.core.registry import (
    PyPIClient,
    NPMClient,
    CratesIOClient,
    get_registry_client,
    PackageCandidate
)


@pytest.mark.integration
def test_pypi_get_package_info():
    """Test fetching package info from PyPI."""
    client = PyPIClient()

    result = client.get_package_info('requests')

    assert result is not None
    assert result.name == 'requests'
    assert result.registry == 'pypi'
    assert result.description is not None
    assert result.version is not None


@pytest.mark.integration
def test_pypi_package_not_found():
    """Test PyPI returns None for non-existent package."""
    client = PyPIClient()

    result = client.get_package_info('this-package-definitely-does-not-exist-12345')

    assert result is None


@pytest.mark.integration
def test_npm_search():
    """Test searching npm registry."""
    client = NPMClient()

    results = client.search('express', max_results=5)

    assert len(results) > 0
    assert any(r.name == 'express' for r in results)
    # All results should be npm
    assert all(r.registry == 'npm' for r in results)


@pytest.mark.integration
def test_npm_get_package_info():
    """Test fetching package info from npm."""
    client = NPMClient()

    result = client.get_package_info('express')

    assert result is not None
    assert result.name == 'express'
    assert result.registry == 'npm'
    assert result.description is not None


@pytest.mark.integration
def test_crates_io_search():
    """Test searching crates.io."""
    client = CratesIOClient()

    results = client.search('serde', max_results=5)

    assert len(results) > 0
    # All results should be cargo
    assert all(r.registry == 'cargo' for r in results)


@pytest.mark.integration
def test_crates_io_get_package_info():
    """Test fetching crate info from crates.io."""
    client = CratesIOClient()

    result = client.get_package_info('serde')

    assert result is not None
    assert result.name == 'serde'
    assert result.registry == 'cargo'


def test_validate_github_url():
    """Test GitHub URL validation."""
    client = PyPIClient()

    # Valid URLs
    assert client.validate_github_url('https://github.com/psf/requests') is not None
    assert client.validate_github_url('https://github.com/owner/repo/') is not None

    # Invalid URLs
    assert client.validate_github_url('http://github.com/owner/repo') is None
    assert client.validate_github_url('https://gitlab.com/owner/repo') is None
    assert client.validate_github_url('not-a-url') is None
    assert client.validate_github_url('') is None


def test_validate_github_url_strips_git_suffix():
    """Test that .git suffix is removed."""
    client = PyPIClient()

    result = client.validate_github_url('https://github.com/owner/repo.git')

    assert result == 'https://github.com/owner/repo'


def test_get_registry_client():
    """Test getting correct registry client for language."""
    assert isinstance(get_registry_client('python'), PyPIClient)
    assert isinstance(get_registry_client('javascript'), NPMClient)
    assert isinstance(get_registry_client('typescript'), NPMClient)
    assert isinstance(get_registry_client('rust'), CratesIOClient)
    assert isinstance(get_registry_client('cargo'), CratesIOClient)


def test_get_registry_client_unknown_language():
    """Test that unknown language raises error."""
    with pytest.raises(ValueError, match="Unsupported language"):
        get_registry_client('cobol')


def test_package_candidate_structure():
    """Test PackageCandidate dataclass structure."""
    candidate = PackageCandidate(
        name='test-package',
        registry='pypi',
        description='A test package',
        version='1.0.0',
        github_url='https://github.com/test/package',
    )

    assert candidate.name == 'test-package'
    assert candidate.registry == 'pypi'
    assert candidate.maintainers == []  # Default empty list


@pytest.mark.integration
def test_npm_extract_github_url():
    """Test extracting GitHub URL from npm package."""
    client = NPMClient()

    result = client.get_package_info('express')

    if result and result.github_url:
        assert 'github.com' in result.github_url
        assert result.github_url.startswith('https://')


@pytest.mark.integration
def test_pypi_extract_maintainers():
    """Test extracting maintainers from PyPI."""
    client = PyPIClient()

    result = client.get_package_info('requests')

    if result:
        # Should have author or maintainer
        assert result.author or result.maintainers