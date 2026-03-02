"""Shared utility functions for priorart core."""

import re
from typing import Optional


def validate_github_url(url: str) -> Optional[str]:
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
    url = url.replace('git+', '').replace('git://', 'https://')

    # Security: Only accept GitHub URLs
    pattern = r'^https://github\.com/([^/]+)/([^/]+)/?.*$'
    match = re.match(pattern, url)

    if match:
        owner, repo = match.groups()
        # Remove .git suffix if present
        repo = repo.rstrip('/').removesuffix('.git')
        return f"https://github.com/{owner}/{repo}"

    return None