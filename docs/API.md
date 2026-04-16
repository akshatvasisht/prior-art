# API Documentation

## Python API

### `find_alternatives()`

Discover and evaluate packages for a given task.

```python
from priorart import find_alternatives

result = find_alternatives(
    language="python",
    task_description="http client",
    explain=False
)
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `language` | `str` | Yes | `"python"`, `"javascript"`, `"typescript"`, `"rust"`, `"go"` |
| `task_description` | `str` | Yes | Natural language task description (e.g., "http client", "rate limiter") |
| `explain` | `bool` | No | Include per-dimension score breakdowns (default: `False`) |

**Returns:** `dict`

```python
{
    "status": "success",
    "count": 5,
    "packages": [
        {
            "name": "requests",
            "full_name": "psf/requests",
            "url": "https://github.com/psf/requests",
            "package_name": "requests",
            "registry": "pypi",
            "description": "Python HTTP for Humans",
            "health_score": 82,
            "recommendation": "use_existing",  # "use_existing" | "evaluate" | "build"
            "identity_verified": True,
            "age_years": 13.2,
            "weekly_downloads": 50000000,
            "license": "Apache-2.0",
            "license_warning": False,
            "dep_health_flag": False,
            "likely_abandoned": False,
            "score_breakdown": {  # Only if explain=True
                "reliability": 85,
                "adoption": 95,
                "versioning": 80,
                "activity_regularity": 75,
                "dependency_health": 90
            }
        }
    ],
    "service_note": null
}
```

**Error Responses:**

```python
{"status": "no_taxonomy_match", "message": "...", "hint": "Pass a concrete noun..."}
{"status": "no_results", "message": "No packages found matching '...'"}
{"status": "below_threshold", "message": "All candidates were below minimum download/star thresholds"}
```

---

### `ingest_repo()`

Clone a repository and extract interface documentation.

```python
from priorart import ingest_repo

result = ingest_repo(
    repo_url="https://github.com/psf/requests",
    language="python"  # Optional
)
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `repo_url` | `str` | Yes | GitHub repository URL |
| `language` | `str` | No | Language for file prioritization |
| `category` | `str` | No | Package category for file prioritization |

**Returns:** `dict`

```python
{
    "status": "success",
    "content": "# Repository: requests\n\n## README\n...\n\n## Key Files\n...",
    "files_included": ["README.md", "requests/__init__.py", "requests/api.py"],
    "files_skipped": 42,
    "total_chars": 18500,
    "monorepo_warning": null,
    "content_warnings": []
}
```

---

## CLI Reference

### `priorart find`

```bash
priorart find --language LANG --task "DESCRIPTION" [OPTIONS]
```

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--language` | `-l` | Target language | Required |
| `--task` | `-t` | Task description | Required |
| `--explain` | | Show dimension scores | `false` |
| `--json` | | Output JSON | `false` |

### `priorart ingest`

```bash
priorart ingest REPO_URL [OPTIONS]
```

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--language` | `-l` | Target language | Optional |
| `--category` | `-c` | Package category | Optional |
| `--json` | | Output JSON | `false` |
| `--verbose` | `-v` | Verbose logging | `false` |

### `priorart cache-info`

Shows cache location, entry count, and size.

```bash
priorart cache-info
```

### `priorart cache-clear`

Deletes the cache database file.

```bash
priorart cache-clear
```

---

## MCP Server

The MCP server exposes `find_alternatives` and `ingest_repo` as tools with the same parameters and return values as the Python API.

```json
{"language": "python", "task_description": "http client", "explain": false}
{"repo_url": "https://github.com/psf/requests", "language": "python"}
```

---

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `GITHUB_TOKEN` | GitHub Personal Access Token | Yes (for GitHub signals) |

Without `GITHUB_TOKEN`, GitHub signals are skipped and packages score with registry/deps.dev data only.

---

## Rate Limits

- **GitHub API:** 5,000 requests/hour with authentication. Registry-first architecture minimizes usage.
- **Registry APIs (PyPI, npm, crates.io):** No authentication required, no rate limits.
