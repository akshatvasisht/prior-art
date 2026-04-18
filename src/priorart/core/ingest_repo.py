"""
Repository ingestion tool for extracting package documentation and interfaces.

Call this tool on ONE candidate at a time - not all candidates.
The agent should call this after narrowing down to 1-2 top candidates.
"""

import logging
from typing import Any

from .ingestion import RepositoryIngester
from .utils import load_config, validate_github_url

# Default priority file patterns per language — a best-effort hint for the ingester.
_DEFAULT_PRIORITY_FILES: dict[str, list[str]] = {
    "python": ["README*", "*.md", "pyproject.toml", "setup.py", "setup.cfg", "src/**/__init__.py"],
    "javascript": ["README*", "*.md", "package.json", "index.js", "src/index.js", "lib/index.js"],
    "typescript": ["README*", "*.md", "package.json", "index.ts", "src/index.ts", "*.d.ts"],
    "go": ["README*", "*.md", "go.mod", "doc.go", "*.go"],
    "rust": ["README*", "*.md", "Cargo.toml", "src/lib.rs", "src/main.rs"],
}

logger = logging.getLogger(__name__)


def ingest_repo(
    repo_url: str, language: str | None = None, category: str | None = None
) -> dict[str, Any]:
    """Ingest a GitHub repository to understand its public interface.

    IMPORTANT: Call this on ONE candidate at a time, not all candidates.
    Use this after find_alternatives has returned 1-2 top candidates that
    you want to deeply understand before making a final decision.

    This tool:
    - Clones the repository (shallow, with timeout/size limits)
    - Detects monorepos and attempts subdirectory resolution
    - Prioritizes README, type stubs, entry points, and changelogs
    - Extracts public interfaces using AST parsing
    - Scans for and redacts prompt injection attempts
    - Respects character budget constraints

    Args:
        repo_url: GitHub repository URL (https://github.com/owner/repo)
        language: Programming language for prioritization (optional)
        category: Package category for file prioritization (optional)

    Returns:
        Dictionary with:
        - content: Extracted repository content
        - files_included: List of files included
        - files_skipped: List of files skipped
        - total_chars: Total characters extracted
        - monorepo_warning: True if monorepo detected but not resolved
        - content_warnings: List of security warnings (prompt injection, etc.)
    """
    try:
        # Load configuration
        config = load_config()

        normalized_url = validate_github_url(repo_url)
        if not normalized_url:
            return {
                "status": "error",
                "message": "Invalid repository URL. Must be https://github.com/owner/repo format.",
            }

        # Initialize ingester
        ingester = RepositoryIngester(
            char_budget=config["ingestion"]["char_budget"],
            max_repo_mb=config["ingestion"]["ingest_max_repo_mb"],
            timeout_seconds=config["ingestion"]["ingest_timeout_seconds"],
        )

        # Get priority files based on language. The `category` parameter is accepted
        # for backwards compatibility with MCP callers but no longer affects selection.
        priority_files = None
        if language:
            priority_files = _DEFAULT_PRIORITY_FILES.get(language.lower())

        # Ingest repository
        result = ingester.ingest(normalized_url, priority_files)

        # Format response
        response = {
            "status": "success",
            "content": result.content,
            "files_included": result.files_included,
            "files_skipped": result.files_skipped,
            "total_chars": result.total_chars,
            "monorepo_warning": result.monorepo_warning,
            "content_warnings": result.content_warnings,
        }

        # Add warnings to message if present
        if result.monorepo_warning:
            response["message"] = (
                "Monorepo detected but subdirectory could not be automatically resolved. "
                "Only root documentation files were included."
            )

        if result.content_warnings:
            warning_msg = "Security warnings: " + "; ".join(result.content_warnings)
            response["security_message"] = warning_msg

        return response

    except ValueError as e:
        # Expected errors (validation, size limits, etc.)
        return {"status": "error", "message": str(e)}

    except RuntimeError as e:
        # Runtime errors (clone failures, timeouts, etc.)
        return {"status": "error", "message": f"Repository ingestion failed: {str(e)}"}

    except Exception as e:
        # Unexpected errors
        logger.error(f"Unexpected error in ingest_repo: {e}", exc_info=True)
        return {"status": "error", "message": f"An unexpected error occurred: {str(e)}"}
