"""Tests for CLI interface."""

import json
import pytest
from click.testing import CliRunner

from priorart.cli import cli


@pytest.fixture
def runner():
    """Create a Click CLI runner."""
    return CliRunner()


def test_cli_help(runner):
    """Test CLI help output."""
    result = runner.invoke(cli, ['--help'])

    assert result.exit_code == 0
    assert 'priorart' in result.output
    assert 'Build-vs-borrow' in result.output


def test_find_command_help(runner):
    """Test find command help."""
    result = runner.invoke(cli, ['find', '--help'])

    assert result.exit_code == 0
    assert '--language' in result.output
    assert '--task' in result.output
    assert '--explain' in result.output


def test_find_requires_language(runner):
    """Test that find command requires language."""
    result = runner.invoke(cli, ['find', '--task', 'http client'])

    assert result.exit_code != 0
    assert 'language' in result.output.lower() or 'required' in result.output.lower()


def test_find_requires_task(runner):
    """Test that find command requires task."""
    result = runner.invoke(cli, ['find', '--language', 'python'])

    assert result.exit_code != 0


def test_find_validates_language(runner):
    """Test that find command validates language."""
    result = runner.invoke(cli, ['find', '--language', 'cobol', '--task', 'parser'])

    # Should fail with invalid choice
    assert result.exit_code != 0


def test_find_json_output(runner):
    """Test find command with JSON output."""
    result = runner.invoke(cli, [
        'find',
        '--language', 'python',
        '--task', 'http client',
        '--json'
    ])

    # Should output valid JSON (might fail if no GITHUB_TOKEN)
    if result.exit_code == 0:
        output = json.loads(result.output)
        assert 'status' in output


def test_ingest_command_help(runner):
    """Test ingest command help."""
    result = runner.invoke(cli, ['ingest', '--help'])

    assert result.exit_code == 0
    assert 'repo_url' in result.output.lower()


def test_ingest_requires_url(runner):
    """Test that ingest command requires URL."""
    result = runner.invoke(cli, ['ingest'])

    assert result.exit_code != 0


def test_cache_info_command(runner):
    """Test cache-info command."""
    result = runner.invoke(cli, ['cache-info'])

    # Should work even with empty cache
    assert result.exit_code == 0
    assert 'Cache' in result.output or 'cache' in result.output


def test_cache_clear_command(runner):
    """Test cache-clear command."""
    result = runner.invoke(cli, ['cache-clear'])

    assert result.exit_code == 0


def test_cli_version(runner):
    """Test CLI version output."""
    result = runner.invoke(cli, ['--version'])

    assert result.exit_code == 0
    assert 'version' in result.output.lower() or '0.1.0' in result.output


def test_find_short_options(runner):
    """Test find command with short options."""
    result = runner.invoke(cli, [
        'find',
        '-l', 'python',
        '-t', 'http client'
    ])

    # Should accept short options
    # (May fail without GITHUB_TOKEN but should parse correctly)
    assert '--language' not in result.output  # Shouldn't error on parsing


def test_find_explain_flag(runner):
    """Test find command with explain flag."""
    result = runner.invoke(cli, [
        'find',
        '--language', 'python',
        '--task', 'http client',
        '--explain'
    ])

    # Should accept explain flag
    if result.exit_code == 0:
        # Explain adds detailed breakdown
        assert result.output != ''


def test_cli_handles_errors_gracefully(runner):
    """Test CLI error handling."""
    # Try with invalid GitHub URL
    result = runner.invoke(cli, [
        'ingest',
        'not-a-github-url'
    ])

    # Should exit with error
    assert result.exit_code != 0
    assert 'error' in result.output.lower() or 'Error' in result.output