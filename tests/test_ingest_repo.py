"""Tests for ingest_repo orchestration."""

from unittest.mock import patch

from priorart.core.ingest_repo import ingest_repo
from priorart.core.ingestion import IngestionResult


def test_ingest_repo_invalid_url():
    """Invalid URL returns error dict without raising."""
    result = ingest_repo("not-a-url")

    assert result["status"] == "error"
    assert "Invalid" in result["message"] or "invalid" in result["message"]


def test_ingest_repo_non_github_url():
    """Non-GitHub URL returns error."""
    result = ingest_repo("https://gitlab.com/owner/repo")

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_URL = "https://github.com/psf/requests"


def _make_result(**overrides):
    """Create a minimal IngestionResult with sensible defaults."""
    defaults = {
        "content": "# README\nHello world",
        "files_included": ["README.md"],
        "files_skipped": ["huge_blob.bin"],
        "total_chars": 20,
        "monorepo_warning": False,
        "content_warnings": [],
    }
    defaults.update(overrides)
    return IngestionResult(**defaults)


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------


@patch("priorart.core.ingest_repo.RepositoryIngester")
@patch("priorart.core.ingest_repo.load_config")
def test_success_basic(mock_load_config, mock_ingester_cls):
    """Basic success path returns all expected keys."""
    mock_load_config.return_value = {
        "ingestion": {
            "char_budget": 50_000,
            "ingest_max_repo_mb": 100,
            "ingest_timeout_seconds": 60,
        }
    }
    ingestion_result = _make_result()
    mock_ingester_cls.return_value.ingest.return_value = ingestion_result

    result = ingest_repo(VALID_URL)

    assert result["status"] == "success"
    assert result["content"] == ingestion_result.content
    assert result["files_included"] == ingestion_result.files_included
    assert result["files_skipped"] == ingestion_result.files_skipped
    assert result["total_chars"] == ingestion_result.total_chars
    assert result["monorepo_warning"] is False
    assert result["content_warnings"] == []
    assert "message" not in result
    assert "security_message" not in result

    mock_ingester_cls.assert_called_once_with(
        char_budget=50_000, max_repo_mb=100, timeout_seconds=60
    )
    mock_ingester_cls.return_value.ingest.assert_called_once_with(
        "https://github.com/psf/requests", None
    )


@patch("priorart.core.ingest_repo.RepositoryIngester")
@patch("priorart.core.ingest_repo.load_config")
def test_success_monorepo_warning(mock_load_config, mock_ingester_cls):
    """Monorepo warning adds a message to the response."""
    mock_load_config.return_value = {
        "ingestion": {
            "char_budget": 50_000,
            "ingest_max_repo_mb": 100,
            "ingest_timeout_seconds": 60,
        }
    }
    mock_ingester_cls.return_value.ingest.return_value = _make_result(monorepo_warning=True)

    result = ingest_repo(VALID_URL)

    assert result["status"] == "success"
    assert result["monorepo_warning"] is True
    assert "message" in result
    assert "Monorepo detected" in result["message"]


@patch("priorart.core.ingest_repo.RepositoryIngester")
@patch("priorart.core.ingest_repo.load_config")
def test_success_content_warnings(mock_load_config, mock_ingester_cls):
    """Content warnings produce a security_message."""
    mock_load_config.return_value = {
        "ingestion": {
            "char_budget": 50_000,
            "ingest_max_repo_mb": 100,
            "ingest_timeout_seconds": 60,
        }
    }
    warnings = ["Prompt injection detected in setup.py"]
    mock_ingester_cls.return_value.ingest.return_value = _make_result(content_warnings=warnings)

    result = ingest_repo(VALID_URL)

    assert result["status"] == "success"
    assert result["content_warnings"] == warnings
    assert "security_message" in result
    assert "Prompt injection detected" in result["security_message"]


# ---------------------------------------------------------------------------
# Language + category (QueryMapper path)
# ---------------------------------------------------------------------------


@patch("priorart.core.ingest_repo.QueryMapper")
@patch("priorart.core.ingest_repo.RepositoryIngester")
@patch("priorart.core.ingest_repo.load_config")
def test_success_with_language_and_category(mock_load_config, mock_ingester_cls, mock_qm_cls):
    """When language+category provided, QueryMapper supplies priority_files."""
    mock_load_config.return_value = {
        "ingestion": {
            "char_budget": 50_000,
            "ingest_max_repo_mb": 100,
            "ingest_timeout_seconds": 60,
        }
    }
    mock_qm_cls.return_value.get_priority_files.return_value = [
        "src/**/*.py",
        "setup.py",
    ]
    mock_ingester_cls.return_value.ingest.return_value = _make_result()

    result = ingest_repo(VALID_URL, language="python", category="http-client")

    assert result["status"] == "success"
    mock_qm_cls.return_value.get_priority_files.assert_called_once_with("http-client", "python")
    mock_ingester_cls.return_value.ingest.assert_called_once_with(
        "https://github.com/psf/requests", ["src/**/*.py", "setup.py"]
    )


@patch("priorart.core.ingest_repo.QueryMapper")
@patch("priorart.core.ingest_repo.RepositoryIngester")
@patch("priorart.core.ingest_repo.load_config")
def test_query_mapper_failure_falls_back_gracefully(
    mock_load_config, mock_ingester_cls, mock_qm_cls
):
    """If QueryMapper raises, priority_files stays None and ingestion proceeds."""
    mock_load_config.return_value = {
        "ingestion": {
            "char_budget": 50_000,
            "ingest_max_repo_mb": 100,
            "ingest_timeout_seconds": 60,
        }
    }
    mock_qm_cls.return_value.get_priority_files.side_effect = KeyError("unknown category")
    mock_ingester_cls.return_value.ingest.return_value = _make_result()

    result = ingest_repo(VALID_URL, language="python", category="nonexistent")

    assert result["status"] == "success"
    # priority_files should be None because the exception was caught
    mock_ingester_cls.return_value.ingest.assert_called_once_with(
        "https://github.com/psf/requests", None
    )


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


@patch("priorart.core.ingest_repo.RepositoryIngester")
@patch("priorart.core.ingest_repo.load_config")
def test_value_error_from_ingester(mock_load_config, mock_ingester_cls):
    """ValueError is returned as a plain error message."""
    mock_load_config.return_value = {
        "ingestion": {
            "char_budget": 50_000,
            "ingest_max_repo_mb": 100,
            "ingest_timeout_seconds": 60,
        }
    }
    mock_ingester_cls.return_value.ingest.side_effect = ValueError("Repository exceeds size limit")

    result = ingest_repo(VALID_URL)

    assert result["status"] == "error"
    assert result["message"] == "Repository exceeds size limit"


@patch("priorart.core.ingest_repo.RepositoryIngester")
@patch("priorart.core.ingest_repo.load_config")
def test_runtime_error_from_ingester(mock_load_config, mock_ingester_cls):
    """RuntimeError is wrapped with 'Repository ingestion failed' prefix."""
    mock_load_config.return_value = {
        "ingestion": {
            "char_budget": 50_000,
            "ingest_max_repo_mb": 100,
            "ingest_timeout_seconds": 60,
        }
    }
    mock_ingester_cls.return_value.ingest.side_effect = RuntimeError("git clone timed out")

    result = ingest_repo(VALID_URL)

    assert result["status"] == "error"
    assert "Repository ingestion failed" in result["message"]
    assert "git clone timed out" in result["message"]


@patch("priorart.core.ingest_repo.RepositoryIngester")
@patch("priorart.core.ingest_repo.load_config")
def test_unexpected_exception_from_ingester(mock_load_config, mock_ingester_cls):
    """Generic Exception is wrapped with 'unexpected error' prefix."""
    mock_load_config.return_value = {
        "ingestion": {
            "char_budget": 50_000,
            "ingest_max_repo_mb": 100,
            "ingest_timeout_seconds": 60,
        }
    }
    mock_ingester_cls.return_value.ingest.side_effect = OSError("disk full")

    result = ingest_repo(VALID_URL)

    assert result["status"] == "error"
    assert "unexpected error" in result["message"].lower()
    assert "disk full" in result["message"]
