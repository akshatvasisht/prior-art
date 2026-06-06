"""Tests for priorart.server — FastMCP tool registration and error contracts.

The three MCP tool handlers (`find_alternatives`,
`evaluate_package`, `ingest_repo`) raise on error so FastMCP wraps them as
`CallToolResult{isError: true}` per the MCP spec, rather than returning a
custom `{"status": "error", ...}` body. These tests pin both the registration
and the raise-on-error contract.
"""

import asyncio
from unittest.mock import patch

import pytest

from priorart import server

# --- registration ---


def test_tools_registered():
    """All three MCP tools must be registered on the FastMCP app at import time."""
    tools = asyncio.run(server.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {"find_alternatives", "evaluate_package", "ingest_repo"} <= names


# --- raise-on-error contract (MCP-spec compliant) ---


def test_find_alternatives_tool_raises_on_failure():
    """When the core fn raises, the handler must propagate so FastMCP can mark isError."""
    with patch.object(server, "core_find_alternatives", side_effect=RuntimeError("boom-find")):
        with pytest.raises(RuntimeError, match="boom-find"):
            server.find_alternatives("python", "http client", False, False)


def test_evaluate_package_tool_raises_on_failure():
    """When the core fn raises, the handler must propagate so FastMCP can mark isError."""
    with patch.object(server, "core_inspect_package", side_effect=ValueError("boom-eval")):
        with pytest.raises(ValueError, match="boom-eval"):
            server.evaluate_package("requests", "python", False)


def test_ingest_repo_tool_raises_on_failure():
    """When the core fn raises, the handler must propagate so FastMCP can mark isError."""
    with patch.object(server, "core_ingest_repo", side_effect=RuntimeError("boom-ingest")):
        with pytest.raises(RuntimeError, match="boom-ingest"):
            server.ingest_repo("https://github.com/psf/requests", "python", None)


# --- success-path contract ---


def test_find_alternatives_tool_returns_success_payload():
    """Happy path — handler must return whatever the core fn returns, unchanged."""
    payload = {
        "status": "ok",
        "packages": [{"name": "requests", "health_score": 88}],
    }
    with patch.object(server, "core_find_alternatives", return_value=payload) as core:
        result = server.find_alternatives("python", "http client", False, False)
    assert result is payload
    core.assert_called_once_with("python", "http client", False, lite=False)


# --- core error-dict is surfaced as a real error ---


def test_raise_if_error_raises_on_error_dict():
    with pytest.raises(RuntimeError, match="kaboom"):
        server._raise_if_error({"status": "error", "message": "kaboom"})


def test_raise_if_error_passes_through_non_error():
    payload = {"status": "ok", "packages": []}
    assert server._raise_if_error(payload) is payload


def test_find_alternatives_tool_raises_on_core_error_dict():
    """A core fn that *returns* an error dict must still become an MCP error."""
    err = {"status": "error", "message": "core-boom"}
    with patch.object(server, "core_find_alternatives", return_value=err):
        with pytest.raises(RuntimeError, match="core-boom"):
            server.find_alternatives("python", "http client", False, False)


def test_ingest_repo_tool_raises_on_core_error_dict():
    err = {"status": "error", "message": "core-ingest-boom"}
    with patch.object(server, "core_ingest_repo", return_value=err):
        with pytest.raises(RuntimeError, match="core-ingest-boom"):
            server.ingest_repo("https://github.com/psf/requests", "python", None)


def test_evaluate_package_tool_raises_on_core_error_dict():
    err = {"status": "error", "message": "core-eval-boom"}
    with patch.object(server, "core_inspect_package", return_value=err):
        with pytest.raises(RuntimeError, match="core-eval-boom"):
            server.evaluate_package("requests", "python", False)
