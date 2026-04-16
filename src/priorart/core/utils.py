"""Shared utility functions for priorart core."""

import logging
import re
from importlib.resources import files
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def validate_github_url(url: str) -> str | None:
    """Validate and normalize GitHub URL.

    Args:
        url: URL to validate

    Returns:
        Normalized GitHub URL if valid, None otherwise.
        Only accepts https://github.com/{owner}/{repo} format.
    """
    if not url:
        return None

    # Clean common git URL patterns
    url = url.replace("git+", "").replace("git://", "https://")

    # Security: Only accept GitHub URLs
    pattern = r"^https://github\.com/([^/]+)/([^/]+)/?.*$"
    match = re.match(pattern, url)

    if match:
        owner, repo = match.groups()
        # Remove .git suffix if present
        repo = repo.rstrip("/").removesuffix(".git")
        return f"https://github.com/{owner}/{repo}"

    return None


def parse_github_url(url: str) -> tuple[str, str] | None:
    """Parse GitHub URL into (owner, repo) tuple.

    Args:
        url: GitHub URL

    Returns:
        Tuple of (owner, repo) or None if invalid.
    """
    match = re.match(r"^https://github\.com/([^/]+)/([^/]+)/?.*$", url)
    if match:
        owner, repo = match.groups()
        repo = repo.rstrip("/").removesuffix(".git")
        return owner, repo
    return None


def load_config() -> dict[str, Any]:
    """Load configuration from bundled config.yaml.

    Returns:
        Configuration dictionary.

    Raises:
        Exception: If config file is missing or invalid YAML.
        ValueError: If scoring weights don't sum to 1.0.
    """
    try:
        config_text = files("priorart.data").joinpath("config.yaml").read_text()
        config = yaml.safe_load(config_text)

        # Validate weights
        weight_sum = sum(config["weights"].values())
        if abs(weight_sum - 1.0) > 0.01:
            raise ValueError(f"Scoring weights must sum to 1.0, got {weight_sum}")

        return config

    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        raise
