"""Tests for the OpenSSF Scorecard client."""

from unittest.mock import MagicMock, patch

import httpx

from priorart.core.scorecard_client import ScorecardClient, ScorecardResult


def test_close_method():
    """close() delegates to the underlying httpx client."""
    client = ScorecardClient()
    client.client = MagicMock()
    client.close()
    client.client.close.assert_called_once()


def test_fetch_404_returns_unavailable():
    """A 404 response yields an empty, unavailable ScorecardResult."""
    with ScorecardClient() as sc:
        mock_response = MagicMock()
        mock_response.status_code = 404
        sc.client = MagicMock()
        sc.client.get.return_value = mock_response

        result = sc.fetch("some", "repo")
        assert isinstance(result, ScorecardResult)
        assert result.available is False
        assert result.overall_score is None


def test_fetch_network_error_returns_unavailable():
    """Exceptions during fetch are caught and yield unavailable result."""
    with ScorecardClient() as sc:
        sc.client = MagicMock()
        sc.client.get.side_effect = httpx.ConnectError("boom")

        result = sc.fetch("some", "repo")
        assert isinstance(result, ScorecardResult)
        assert result.available is False


def test_fetch_success_aggregates():
    """Happy path: valid JSON is aggregated into a populated result."""
    payload = {
        "score": 8.5,
        "checks": [
            {"name": "Code-Review", "score": 10},
            {"name": "CI-Tests", "score": 8},
            {"name": "Vulnerabilities", "score": 10},
            {"name": "SBOM", "score": 6},
        ],
    }
    with ScorecardClient() as sc:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = payload
        mock_response.raise_for_status = MagicMock()
        sc.client = MagicMock()
        sc.client.get.return_value = mock_response

        result = sc.fetch("owner", "repo")
        assert result.available is True
        assert result.overall_score == 8.5
        assert result.reliability_bucket is not None
        assert result.dep_health_bucket is not None


def test_aggregate_skips_none_score():
    """_aggregate skips checks that have no score or no name."""
    payload = {
        "score": -1,  # not-applicable overall → overall_score stays None
        "checks": [
            {"name": "Code-Review", "score": None},  # skipped
            {"name": None, "score": 5},  # skipped
            {"name": "CI-Tests", "score": 9},
        ],
    }
    result = ScorecardClient._aggregate(payload)
    assert "Code-Review" not in result.checks
    assert result.checks == {"CI-Tests": 9}
    assert result.overall_score is None


def test_bucket_returns_none_with_no_applicable_checks():
    """When all bucket checks are -1 (not applicable), the bucket is None."""
    payload = {
        "checks": [
            {"name": "Code-Review", "score": -1},
            {"name": "CI-Tests", "score": -1},
            {"name": "Fuzzing", "score": -1},
            {"name": "SAST", "score": -1},
            {"name": "Signed-Releases", "score": -1},
            {"name": "Branch-Protection", "score": -1},
            {"name": "Maintained", "score": -1},
            {"name": "Security-Policy", "score": -1},
        ],
    }
    result = ScorecardClient._aggregate(payload)
    assert result.reliability_bucket is None
    assert result.dep_health_bucket is None


def test_context_manager_closes_client():
    with patch("priorart.core.scorecard_client.httpx.Client") as client_cls:
        instance = MagicMock()
        client_cls.return_value = instance
        with ScorecardClient() as sc:
            assert sc.client is instance
        instance.close.assert_called_once()
