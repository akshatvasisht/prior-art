"""Tests for priorart.core.inspect — single-package evaluation."""

from unittest.mock import MagicMock, patch

import pytest

from priorart.core.inspect import _infer_registry, inspect_package

# --- _infer_registry ---


def test_infer_registry_explicit_python():
    assert _infer_registry("python", "x") == ("python", "pypi")


def test_infer_registry_explicit_javascript():
    assert _infer_registry("javascript", "x") == ("javascript", "npm")


def test_infer_registry_explicit_typescript():
    assert _infer_registry("typescript", "x") == ("typescript", "npm")


def test_infer_registry_explicit_rust():
    assert _infer_registry("rust", "x") == ("rust", "cargo")


def test_infer_registry_explicit_go():
    assert _infer_registry("go", "x") == ("go", "go")


def test_infer_registry_explicit_golang():
    assert _infer_registry("golang", "x") == ("golang", "go")


def test_infer_registry_unsupported_language_raises():
    with pytest.raises(ValueError, match="Unsupported language"):
        _infer_registry("cobol", "x")


def test_infer_registry_heuristic_scoped_npm():
    assert _infer_registry(None, "@tanstack/query") == ("javascript", "npm")


def test_infer_registry_heuristic_slash_npm():
    assert _infer_registry(None, "lodash/fp") == ("javascript", "npm")


def test_infer_registry_heuristic_github_go():
    assert _infer_registry(None, "github.com/spf13/cobra") == ("go", "go")


def test_infer_registry_heuristic_golang_org():
    assert _infer_registry(None, "golang.org/x/tools") == ("go", "go")


def test_infer_registry_heuristic_bare_python():
    assert _infer_registry(None, "requests") == ("python", "pypi")


# --- inspect_package ---


def _make_scored(with_breakdown: bool = False) -> MagicMock:
    scored = MagicMock()
    scored.name = "requests"
    scored.full_name = "psf/requests"
    scored.url = "https://github.com/psf/requests"
    scored.package_name = "requests"
    scored.registry = "pypi"
    scored.description = "HTTP for Humans"
    scored.health_score = 82
    scored.recommendation = "use_existing"
    scored.identity_verified = True
    scored.age_years = 13.2
    scored.weekly_downloads = 45_000_000
    scored.license = "Apache-2.0"
    scored.license_warning = False
    scored.dep_health_flag = False
    scored.likely_abandoned = False
    scored.scorecard_overall = 8.5
    scored.build_cost_weeks = 14
    scored.commodity_tag = "commodity"
    scored.maintenance_liability = "low"
    if with_breakdown:
        breakdown = MagicMock()
        breakdown.reliability = 0.9
        breakdown.adoption = 0.95
        breakdown.versioning = 0.8
        breakdown.activity_regularity = 0.7
        breakdown.dependency_health = 0.85
        scored.score_breakdown = breakdown
    else:
        scored.score_breakdown = None
    return scored


@patch("priorart.core.inspect.PackageScorer")
@patch("priorart.core.inspect.load_config")
@patch("priorart.core.inspect.SQLiteCache")
@patch("priorart.core.inspect.evaluate_candidate")
@patch("priorart.core.inspect.get_registry_client")
def test_inspect_package_success(
    mock_get_client, mock_eval, mock_cache_cls, mock_load_config, mock_scorer_cls
):
    client = MagicMock()
    client.get_package_info.return_value = MagicMock(name="candidate")
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=client)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_get_client.return_value = ctx

    mock_eval.return_value = _make_scored(with_breakdown=False)

    result = inspect_package("requests", language="python", explain=False)

    assert result["status"] == "success"
    assert result["package"]["health_score"] == 82
    assert result["package"]["name"] == "requests"
    assert result["package"]["recommendation"] == "use_existing"
    assert "score_breakdown" not in result["package"]


@patch("priorart.core.inspect.PackageScorer")
@patch("priorart.core.inspect.load_config")
@patch("priorart.core.inspect.SQLiteCache")
@patch("priorart.core.inspect.evaluate_candidate")
@patch("priorart.core.inspect.get_registry_client")
def test_inspect_package_success_with_explain(
    mock_get_client, mock_eval, mock_cache_cls, mock_load_config, mock_scorer_cls
):
    client = MagicMock()
    client.get_package_info.return_value = MagicMock(name="candidate")
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=client)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_get_client.return_value = ctx

    mock_eval.return_value = _make_scored(with_breakdown=True)

    result = inspect_package("requests", language="python", explain=True)

    assert result["status"] == "success"
    bd = result["package"]["score_breakdown"]
    assert bd["reliability"] == 0.9
    assert bd["adoption"] == 0.95
    assert bd["versioning"] == 0.8
    assert bd["activity_regularity"] == 0.7
    assert bd["dependency_health"] == 0.85


@patch("priorart.core.inspect.PackageScorer")
@patch("priorart.core.inspect.load_config")
@patch("priorart.core.inspect.SQLiteCache")
@patch("priorart.core.inspect.evaluate_candidate")
@patch("priorart.core.inspect.get_registry_client")
def test_inspect_package_not_found(
    mock_get_client, mock_eval, mock_cache_cls, mock_load_config, mock_scorer_cls
):
    client = MagicMock()
    client.get_package_info.return_value = None
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=client)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_get_client.return_value = ctx

    result = inspect_package("nonexistent", language="python")

    assert result["status"] == "not_found"
    assert "nonexistent" in result["message"]
    mock_eval.assert_not_called()


@patch("priorart.core.inspect.PackageScorer")
@patch("priorart.core.inspect.load_config")
@patch("priorart.core.inspect.SQLiteCache")
@patch("priorart.core.inspect.evaluate_candidate")
@patch("priorart.core.inspect.get_registry_client")
def test_inspect_package_no_signals(
    mock_get_client, mock_eval, mock_cache_cls, mock_load_config, mock_scorer_cls
):
    client = MagicMock()
    client.get_package_info.return_value = MagicMock(name="candidate")
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=client)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_get_client.return_value = ctx

    mock_eval.return_value = None

    result = inspect_package("lonely", language="python")

    assert result["status"] == "no_signals"
    assert "lonely" in result["message"]


@patch("priorart.core.inspect.PackageScorer")
@patch("priorart.core.inspect.load_config")
@patch("priorart.core.inspect.SQLiteCache")
@patch("priorart.core.inspect.get_registry_client")
def test_inspect_package_exception(
    mock_get_client, mock_cache_cls, mock_load_config, mock_scorer_cls
):
    mock_get_client.side_effect = RuntimeError("boom")

    result = inspect_package("requests", language="python")

    assert result["status"] == "error"
    assert "boom" in result["message"]


@patch("priorart.core.inspect.PackageScorer")
@patch("priorart.core.inspect.load_config")
@patch("priorart.core.inspect.SQLiteCache")
@patch("priorart.core.inspect.evaluate_candidate")
@patch("priorart.core.inspect.get_registry_client")
def test_inspect_package_infers_language(
    mock_get_client, mock_eval, mock_cache_cls, mock_load_config, mock_scorer_cls
):
    client = MagicMock()
    client.get_package_info.return_value = MagicMock(name="candidate")
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=client)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_get_client.return_value = ctx

    mock_eval.return_value = _make_scored()

    inspect_package("requests", language=None)

    mock_get_client.assert_called_once_with("python")
