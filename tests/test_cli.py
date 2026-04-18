"""Tests for CLI interface."""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from priorart.cli import cli


@pytest.fixture(autouse=True)
def _github_token(monkeypatch):
    """Satisfy the `find` command's GITHUB_TOKEN requirement in tests."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")


@pytest.fixture
def runner():
    """Create a Click CLI runner."""
    return CliRunner()


def test_cli_help(runner):
    """Test CLI help output."""
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "priorart" in result.output
    assert "Build-vs-borrow" in result.output


def test_find_command_help(runner):
    """Test find command help."""
    result = runner.invoke(cli, ["find", "--help"])

    assert result.exit_code == 0
    assert "--language" in result.output
    assert "--task" in result.output
    assert "--explain" in result.output


def test_find_requires_language(runner):
    """Test that find command requires language."""
    result = runner.invoke(cli, ["find", "--task", "http client"])

    assert result.exit_code != 0
    assert "language" in result.output.lower() or "required" in result.output.lower()


def test_find_requires_task(runner):
    """Test that find command requires task."""
    result = runner.invoke(cli, ["find", "--language", "python"])

    assert result.exit_code != 0


def test_find_validates_language(runner):
    """Test that find command validates language."""
    result = runner.invoke(cli, ["find", "--language", "cobol", "--task", "parser"])

    # Should fail with invalid choice
    assert result.exit_code != 0


def test_find_json_output(runner):
    """Test find command with JSON output."""
    mock_result = {
        "status": "success",
        "count": 1,
        "packages": [
            {
                "name": "requests",
                "health_score": 85,
                "recommendation": "use_existing",
            }
        ],
    }
    with patch("priorart.cli.find_alternatives", return_value=mock_result):
        result = runner.invoke(
            cli, ["find", "--language", "python", "--task", "http client", "--json"]
        )

    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output["status"] == "success"
    assert output["count"] == 1


def test_ingest_command_help(runner):
    """Test ingest command help."""
    result = runner.invoke(cli, ["ingest", "--help"])

    assert result.exit_code == 0
    assert "repo_url" in result.output.lower()


def test_ingest_requires_url(runner):
    """Test that ingest command requires URL."""
    result = runner.invoke(cli, ["ingest"])

    assert result.exit_code != 0


def test_cache_info_command(runner):
    """Test cache-info command."""
    result = runner.invoke(cli, ["cache-info"])

    # Should work even with empty cache
    assert result.exit_code == 0
    assert "Cache" in result.output or "cache" in result.output


def test_cache_clear_command(runner):
    """Test cache-clear command."""
    result = runner.invoke(cli, ["cache-clear"])

    assert result.exit_code == 0


def test_cli_version(runner):
    """Test CLI version output."""
    result = runner.invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert "version" in result.output.lower() or "0.1.0" in result.output


def test_find_requires_github_token(runner, monkeypatch):
    """find should fail loudly when GITHUB_TOKEN is unset."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    result = runner.invoke(cli, ["find", "-l", "python", "-t", "http client"])

    assert result.exit_code == 1
    assert "GITHUB_TOKEN" in result.output


def test_find_short_options(runner):
    """Test find command with short options."""
    mock_result = {"status": "no_match", "message": "No taxonomy match"}
    with patch("priorart.cli.find_alternatives", return_value=mock_result) as mock_fn:
        result = runner.invoke(cli, ["find", "-l", "python", "-t", "http client"])

    assert result.exit_code == 0
    mock_fn.assert_called_once_with("python", "http client", explain=False, lite=False)


def test_find_explain_flag(runner):
    """Test find command with explain flag."""
    mock_result = {"status": "success", "count": 0, "packages": []}
    with patch("priorart.cli.find_alternatives", return_value=mock_result) as mock_fn:
        result = runner.invoke(
            cli, ["find", "--language", "python", "--task", "http client", "--explain"]
        )

    assert result.exit_code == 0
    mock_fn.assert_called_once_with("python", "http client", explain=True, lite=False)


def test_find_lite_flag(runner):
    """--lite forwards through to find_alternatives as lite=True."""
    mock_result = {"status": "success", "count": 0, "packages": []}
    with patch("priorart.cli.find_alternatives", return_value=mock_result) as mock_fn:
        result = runner.invoke(cli, ["find", "-l", "python", "-t", "http client", "--lite"])

    assert result.exit_code == 0
    mock_fn.assert_called_once_with("python", "http client", explain=False, lite=True)


def test_cli_handles_errors_gracefully(runner):
    """Test CLI shows error message for invalid ingest URL."""
    result = runner.invoke(cli, ["ingest", "not-a-github-url"])

    # ingest_repo returns {"status": "error", ...} without raising,
    # so exit code is 0 but output should contain the error message
    assert result.exit_code == 0
    assert "error" in result.output.lower() or "invalid" in result.output.lower()


# --- _print_find_results human output ---


def test_find_human_output_success(runner):
    """Test human-readable output for successful find_alternatives."""
    mock_result = {
        "status": "success",
        "count": 2,
        "packages": [
            {
                "name": "requests",
                "url": "https://github.com/psf/requests",
                "health_score": 85,
                "recommendation": "use_existing",
                "description": "Python HTTP for Humans",
                "weekly_downloads": 45_000_000,
                "license": "Apache-2.0",
                "license_warning": False,
                "identity_verified": True,
                "likely_abandoned": False,
                "dep_health_flag": False,
                "score_breakdown": {
                    "reliability": 70,
                    "adoption": 90,
                    "versioning": 80,
                    "activity_regularity": 75,
                    "dependency_health": 85,
                },
            },
            {
                "name": "httpx",
                "url": "https://github.com/encode/httpx",
                "health_score": 60,
                "recommendation": "evaluate",
                "description": "A next-generation HTTP client",
                "weekly_downloads": 5_000_000,
                "license": "BSD-3-Clause",
                "license_warning": False,
                "identity_verified": False,
                "likely_abandoned": True,
                "dep_health_flag": True,
            },
        ],
        "service_note": "Consider managed HTTP clients.",
    }
    with patch("priorart.cli.find_alternatives", return_value=mock_result):
        result = runner.invoke(cli, ["find", "--language", "python", "--task", "http client"])

    assert result.exit_code == 0
    assert "requests" in result.output
    assert "85" in result.output
    assert "httpx" in result.output
    assert "45,000,000" in result.output
    assert "Reliability" in result.output  # score breakdown
    assert "Warnings:" in result.output  # httpx has warnings
    assert "Identity not verified" in result.output
    assert "Likely abandoned" in result.output
    assert "Dependency health issues" in result.output
    assert "Consider managed HTTP clients." in result.output


def test_find_human_output_non_success(runner):
    """Test human-readable output for non-success status."""
    mock_result = {
        "status": "no_results",
        "message": "No packages found",
        "service_note": "Try a different query.",
    }
    with patch("priorart.cli.find_alternatives", return_value=mock_result):
        result = runner.invoke(cli, ["find", "--language", "python", "--task", "obscure thing"])

    assert result.exit_code == 0
    assert "no_results" in result.output
    assert "No packages found" in result.output
    assert "Try a different query." in result.output


def test_find_error_exits_1(runner):
    """Test find exits with code 1 when the backend raises."""
    with patch("priorart.cli.find_alternatives", side_effect=Exception("boom")):
        result = runner.invoke(cli, ["find", "--language", "python", "--task", "http"])

    assert result.exit_code == 1
    assert "Error: boom" in result.output


def test_ingest_human_output(runner):
    """Test human-readable output for successful ingest."""
    mock_result = {
        "status": "success",
        "content": "# README\nHello world",
        "files_included": ["README.md", "src/main.py"],
        "files_skipped": ["tests/test_main.py"],
        "total_chars": 2000,
        "monorepo_warning": True,
        "message": "Monorepo detected",
        "content_warnings": ["Potential injection detected"],
    }
    with patch("priorart.cli.ingest_repo", return_value=mock_result):
        result = runner.invoke(cli, ["ingest", "https://github.com/owner/repo"])

    assert result.exit_code == 0
    assert "2,000" in result.output
    assert "Monorepo detected" in result.output
    assert "Potential injection detected" in result.output
    assert "# README" in result.output


def test_ingest_json_output(runner):
    """Test JSON output for ingest command."""
    mock_result = {
        "status": "success",
        "content": "data",
        "files_included": ["a.py"],
        "files_skipped": [],
        "total_chars": 4,
    }
    with patch("priorart.cli.ingest_repo", return_value=mock_result):
        result = runner.invoke(cli, ["ingest", "https://github.com/owner/repo", "--json"])

    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output["status"] == "success"


def test_ingest_error_exits_1(runner):
    """Test ingest exits with code 1 when backend raises."""
    with patch("priorart.cli.ingest_repo", side_effect=RuntimeError("clone failed")):
        result = runner.invoke(cli, ["ingest", "https://github.com/owner/repo"])

    assert result.exit_code == 1
    assert "Error: clone failed" in result.output


def test_cache_info_with_data(runner, tmp_path):
    """Test cache-info with populated cache."""
    import sqlite3

    cache_file = tmp_path / "cache.db"

    with sqlite3.connect(cache_file) as conn:
        conn.execute("""
            CREATE TABLE package_signals (
                package_name TEXT NOT NULL,
                registry TEXT NOT NULL,
                PRIMARY KEY (package_name, registry)
            )
        """)
        conn.execute(
            "INSERT INTO package_signals (package_name, registry) VALUES (?, ?)",
            ("requests", "pypi"),
        )

    with patch("platformdirs.user_cache_dir", return_value=str(tmp_path)):
        result = runner.invoke(cli, ["cache-info"])

    assert result.exit_code == 0
    assert "Cached packages: 1" in result.output


def test_cache_clear_deletes_file(runner, tmp_path):
    """Test cache-clear removes the cache file."""
    cache_file = tmp_path / "cache.db"
    cache_file.write_text("dummy")

    with patch("platformdirs.user_cache_dir", return_value=str(tmp_path)):
        result = runner.invoke(cli, ["cache-clear"])

    assert result.exit_code == 0
    assert "Cache cleared" in result.output
    assert not cache_file.exists()


def test_cache_clear_exception(runner):
    """Test cache-clear handles exceptions."""
    with patch("platformdirs.user_cache_dir", side_effect=RuntimeError("disk error")):
        result = runner.invoke(cli, ["cache-clear"])

    assert result.exit_code == 1
    assert "Error clearing cache" in result.output


def test_cache_info_exception(runner):
    """Test cache-info handles exceptions."""
    with patch("platformdirs.user_cache_dir", side_effect=RuntimeError("disk error")):
        result = runner.invoke(cli, ["cache-info"])

    assert result.exit_code == 1
    assert "Error reading cache" in result.output


def test_find_human_output_license_warning(runner):
    """Test copyleft license warning in output."""
    mock_result = {
        "status": "success",
        "count": 1,
        "packages": [
            {
                "name": "gpl-lib",
                "url": "https://github.com/owner/gpl-lib",
                "health_score": 70,
                "recommendation": "evaluate",
                "description": "GPL library",
                "license": "GPL-3.0",
                "license_warning": True,
                "identity_verified": True,
                "likely_abandoned": False,
                "dep_health_flag": False,
            },
        ],
    }
    with patch("priorart.cli.find_alternatives", return_value=mock_result):
        result = runner.invoke(cli, ["find", "-l", "python", "-t", "test"])

    assert result.exit_code == 0
    assert "(copyleft)" in result.output


def test_ingest_truncated_content(runner):
    """Test ingest truncates content over 5000 chars."""
    long_content = "x" * 6000
    mock_result = {
        "status": "success",
        "content": long_content,
        "files_included": ["big.py"],
        "files_skipped": [],
        "total_chars": 6000,
        "monorepo_warning": False,
    }
    with patch("priorart.cli.ingest_repo", return_value=mock_result):
        result = runner.invoke(cli, ["ingest", "https://github.com/o/r"])

    assert result.exit_code == 0
    assert "truncated" in result.output
    assert "1,000 more characters" in result.output


# --- inspect command ---


def _mock_inspect_package(**overrides):
    """Build a baseline inspect_package success payload."""
    pkg = {
        "name": "requests",
        "full_name": "psf/requests",
        "url": "https://github.com/psf/requests",
        "package_name": "requests",
        "registry": "pypi",
        "description": "HTTP for Humans",
        "health_score": 82,
        "recommendation": "use_existing",
        "identity_verified": True,
        "age_years": 13.2,
        "weekly_downloads": 45_000_000,
        "license": "Apache-2.0",
        "license_warning": False,
        "dep_health_flag": False,
        "likely_abandoned": False,
        "scorecard_overall": 8.5,
        "build_cost_weeks": 14,
        "commodity_tag": "commodity",
        "maintenance_liability": "low",
    }
    pkg.update(overrides)
    return pkg


def test_inspect_success_plain(runner):
    """inspect shows the package name in plain output."""
    mock_result = {"status": "success", "package": _mock_inspect_package()}
    with patch("priorart.cli.inspect_package", return_value=mock_result):
        result = runner.invoke(cli, ["inspect", "requests", "--language", "python"])

    assert result.exit_code == 0
    assert "requests" in result.output


def test_inspect_success_json(runner):
    """inspect --json emits parseable JSON."""
    mock_result = {"status": "success", "package": _mock_inspect_package()}
    with patch("priorart.cli.inspect_package", return_value=mock_result):
        result = runner.invoke(cli, ["inspect", "requests", "--language", "python", "--json"])

    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output["status"] == "success"
    assert output["package"]["name"] == "requests"


def test_inspect_not_found(runner):
    """inspect prints the message when the package is not found."""
    mock_result = {"status": "not_found", "message": "Package does not exist"}
    with patch("priorart.cli.inspect_package", return_value=mock_result):
        result = runner.invoke(cli, ["inspect", "nonexistent"])

    assert result.exit_code == 0
    assert "not_found" in result.output
    assert "Package does not exist" in result.output


def test_inspect_error_exits_nonzero(runner):
    """inspect exits 1 when the backend raises."""
    with patch("priorart.cli.inspect_package", side_effect=Exception("boom")):
        result = runner.invoke(cli, ["inspect", "requests"])

    assert result.exit_code == 1
    assert "boom" in result.output


def test_inspect_with_explain(runner):
    """inspect --explain surfaces score breakdown dimensions."""
    pkg = _mock_inspect_package(
        score_breakdown={
            "reliability": 70,
            "adoption": 90,
            "versioning": 80,
            "activity_regularity": 75,
            "dependency_health": 85,
        }
    )
    mock_result = {"status": "success", "package": pkg}
    with patch("priorart.cli.inspect_package", return_value=mock_result):
        result = runner.invoke(cli, ["inspect", "requests", "--explain"])

    assert result.exit_code == 0
    assert "Reliability" in result.output
    assert "Adoption" in result.output
    assert "Versioning" in result.output
    assert "Activity" in result.output
    assert "Dependencies" in result.output


# --- _print_bvb and _print_inspect_result ---


def test_print_inspect_result_success_no_warnings(runner):
    """Success payload without warning flags emits no Warnings line."""
    pkg = _mock_inspect_package(
        license_warning=False,
        dep_health_flag=False,
        likely_abandoned=False,
        commodity_tag=None,
        build_cost_weeks=None,
        scorecard_overall=None,
        maintenance_liability=None,
    )
    mock_result = {"status": "success", "package": pkg}
    with patch("priorart.cli.inspect_package", return_value=mock_result):
        result = runner.invoke(cli, ["inspect", "requests"])

    assert result.exit_code == 0
    assert "Warnings:" not in result.output
    assert "(copyleft)" not in result.output
    assert "Build-vs-borrow" not in result.output


def test_print_inspect_result_with_warnings(runner):
    """Warning flags trigger the Warnings line."""
    pkg = _mock_inspect_package(
        license_warning=True,
        dep_health_flag=True,
        likely_abandoned=True,
        identity_verified=False,
    )
    mock_result = {"status": "success", "package": pkg}
    with patch("priorart.cli.inspect_package", return_value=mock_result):
        result = runner.invoke(cli, ["inspect", "requests"])

    assert result.exit_code == 0
    assert "Warnings:" in result.output
    assert "Identity not verified" in result.output
    assert "Likely abandoned" in result.output
    assert "Dependency health issues" in result.output
    assert "(copyleft)" in result.output


def test_print_bvb_shows_weeks_and_months(runner):
    """Build cost >= 12 weeks renders as engineer-months."""
    pkg = _mock_inspect_package(build_cost_weeks=26)
    mock_result = {"status": "success", "package": pkg}
    with patch("priorart.cli.inspect_package", return_value=mock_result):
        result = runner.invoke(cli, ["inspect", "requests"])

    assert result.exit_code == 0
    assert "engineer-months" in result.output


def test_print_bvb_shows_weeks_only_small(runner):
    """Build cost < 12 weeks renders as engineer-weeks."""
    pkg = _mock_inspect_package(build_cost_weeks=4)
    mock_result = {"status": "success", "package": pkg}
    with patch("priorart.cli.inspect_package", return_value=mock_result):
        result = runner.invoke(cli, ["inspect", "requests"])

    assert result.exit_code == 0
    assert "engineer-weeks" in result.output
    assert "engineer-months" not in result.output


def test_print_bvb_scorecard_line(runner):
    """Scorecard value appears in build-vs-borrow block."""
    pkg = _mock_inspect_package(scorecard_overall=8.5)
    mock_result = {"status": "success", "package": pkg}
    with patch("priorart.cli.inspect_package", return_value=mock_result):
        result = runner.invoke(cli, ["inspect", "requests"])

    assert result.exit_code == 0
    assert "8.5" in result.output
    assert "OpenSSF Scorecard" in result.output


def test_print_bvb_commodity_and_liability(runner):
    """Commodity tag and maintenance liability render in output."""
    pkg = _mock_inspect_package(commodity_tag="commodity", maintenance_liability="high")
    mock_result = {"status": "success", "package": pkg}
    with patch("priorart.cli.inspect_package", return_value=mock_result):
        result = runner.invoke(cli, ["inspect", "requests"])

    assert result.exit_code == 0
    assert "commodity" in result.output
    assert "high" in result.output


def test_print_inspect_result_not_found(runner):
    """not_found status avoids the BVB block."""
    mock_result = {"status": "not_found", "message": "Package does not exist"}
    with patch("priorart.cli.inspect_package", return_value=mock_result):
        result = runner.invoke(cli, ["inspect", "missing"])

    assert result.exit_code == 0
    assert "Package does not exist" in result.output
    assert "Build-vs-borrow" not in result.output
    assert "Health Score" not in result.output
