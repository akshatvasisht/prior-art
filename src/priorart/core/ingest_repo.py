"""
Repository ingestion tool for extracting package documentation and interfaces.

Call this tool on ONE candidate at a time - not all candidates.
The agent should call this after narrowing down to 1-2 top candidates.
"""

import logging
from typing import Dict, Any, Optional, List
from importlib.resources import files

import yaml

from .ingestion import RepositoryIngester
from .query import QueryMapper

logger = logging.getLogger(__name__)


def load_config() -> Dict[str, Any]:
    """Load configuration from bundled config.yaml.

    Returns:
        Configuration dictionary

    Raises:
        Exception: If config file is missing or invalid.
            Hint: Ensure src/priorart/data/config.yaml exists.
    """
    try:
        config_text = files('priorart.data').joinpath('config.yaml').read_text()
        return yaml.safe_load(config_text)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        raise


def ingest_repo(
    repo_url: str,
    language: Optional[str] = None,
    category: Optional[str] = None
) -> Dict[str, Any]:
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

        # Validate URL
        if not repo_url.startswith('https://github.com/'):
            return {
                "status": "error",
                "message": "Invalid repository URL. Must be https://github.com/owner/repo format."
            }

        # Initialize ingester
        ingester = RepositoryIngester(
            char_budget=config['ingestion']['char_budget'],
            max_repo_mb=config['ingestion']['ingest_max_repo_mb'],
            timeout_seconds=config['ingestion']['ingest_timeout_seconds']
        )

        # Get priority files based on language and category
        priority_files = None
        if language and category:
            try:
                query_mapper = QueryMapper()
                priority_files = query_mapper.get_priority_files(category, language)
            except Exception as e:
                logger.warning(f"Failed to get priority files: {e}")

        # Ingest repository
        result = ingester.ingest(repo_url, priority_files)

        # Format response
        response = {
            "status": "success",
            "content": result.content,
            "files_included": result.files_included,
            "files_skipped": result.files_skipped,
            "total_chars": result.total_chars,
            "monorepo_warning": result.monorepo_warning,
            "content_warnings": result.content_warnings
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
        return {
            "status": "error",
            "message": str(e)
        }

    except RuntimeError as e:
        # Runtime errors (clone failures, timeouts, etc.)
        return {
            "status": "error",
            "message": f"Repository ingestion failed: {str(e)}"
        }

    except Exception as e:
        # Unexpected errors
        logger.error(f"Unexpected error in ingest_repo: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"An unexpected error occurred: {str(e)}"
        }