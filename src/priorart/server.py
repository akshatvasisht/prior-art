"""
FastMCP server for priorart package discovery tool.

Thin wrapper over core functions for MCP protocol compatibility.
"""

import logging

from fastmcp import FastMCP

from .core.find_alternatives import find_alternatives as core_find_alternatives
from .core.ingest_repo import ingest_repo as core_ingest_repo

# Set up logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Initialize MCP server
mcp = FastMCP("priorart")


@mcp.tool()
def find_alternatives(
    language: str, task_description: str, explain: bool = False
) -> dict:  # pragma: no cover
    """Find and score open source packages for a given task.

    Discovers packages via registry APIs, scores them across 5 health dimensions
    (reliability, adoption, versioning, activity, dependencies), and returns top 5
    with recommendations.

    Call when implementing general-purpose capabilities (http clients, parsers, etc.),
    NOT for project-specific logic or when user names a specific library.

    Args:
        language: Programming language (python, javascript, typescript, go, rust)
        task_description: Capability description (e.g., "http client", "jwt parser")
        explain: Include detailed scoring breakdown

    Returns:
        Dict with status, top 5 packages (health_score, recommendation, warnings),
        and optional service_note about managed alternatives.
    """
    try:
        return core_find_alternatives(language, task_description, explain)
    except Exception as e:
        logger.error(f"Error in find_alternatives: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@mcp.tool()
def ingest_repo(
    repo_url: str, language: str | None = None, category: str | None = None
) -> dict:  # pragma: no cover
    """Extract a GitHub repository's public interface for evaluation.

    Call on ONE candidate at a time after find_alternatives returns score 50-74.
    Clones repo, extracts README/interfaces via AST, scans for security issues.

    Limits: 100MB max size, 30s timeout, 24k char budget, GitHub URLs only.

    Args:
        repo_url: GitHub repository URL (https://github.com/owner/repo)
        language: Programming language for file prioritization (optional)
        category: Package category for prioritization (optional)

    Returns:
        Dict with content, files_included/skipped, warnings.
    """
    try:
        return core_ingest_repo(repo_url, language, category)
    except Exception as e:
        logger.error(f"Error in ingest_repo: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


def main() -> None:  # pragma: no cover
    """Main entry point for MCP server."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
