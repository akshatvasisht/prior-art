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
| `language` | `str` | Yes | `"python"`, `"javascript"`, `"typescript"`, `"rust"`, `"go"`, `"java"`, `"kotlin"`, `"scala"`, `"csharp"`, `"dotnet"`, `"fsharp"` |
| `task_description` | `str` | Yes | Natural language task description (e.g., "http client", "rate limiter") |
| `explain` | `bool` | No | Include per-dimension score breakdowns (default: `False`) |
| `lite` | `bool` | No | Skip the semantic index download; use live registry search instead (default: `False`) |

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
{"status": "no_results", "message": "No packages found matching '...'"}
{"status": "below_threshold", "message": "All candidates were below minimum download/star thresholds"}
```

---

### `inspect_package()`

Score a single named package without retrieval.

```python
from priorart import inspect_package

result = inspect_package(
    package_name="requests",
    language="python",   # Optional; inferred from name shape when omitted
    explain=False,
)
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `package_name` | `str` | Yes | Name as it appears on the registry (e.g. `"requests"`, `"@tanstack/query"`, `"github.com/spf13/cobra"`, `"tokio"`) |
| `language` | `str` | No | Language hint; inferred from name shape when omitted |
| `explain` | `bool` | No | Include per-dimension score breakdowns (default: `False`) |

**Returns:** `{"status": "success", "package": {...}}`, where `package` has the same schema as an entry in `find_alternatives.packages`.

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
| `--lite` | | Skip the 120MB index download; use registry search | `false` |

### `priorart inspect`

```bash
priorart inspect PACKAGE_NAME [OPTIONS]
```

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--language` | `-l` | Language hint (inferred from name shape when omitted) | Optional |
| `--explain` | `-e` | Show dimension scores | `false` |
| `--json` | | Output JSON | `false` |
| `--verbose` | `-v` | Verbose logging | `false` |

### `priorart ingest`

```bash
priorart ingest REPO_URL [OPTIONS]
```

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--language` | `-l` | Target language | Optional |
| `--category` | `-c` | Accepted for back-compat; no longer affects file prioritization | Optional |
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

The MCP server exposes `find_alternatives`, `evaluate_package`, and `ingest_repo` as tools with the same parameters and return values as the Python API. (`evaluate_package` is the MCP-side name for `inspect_package`.)

```json
{"language": "python", "task_description": "http client", "explain": false, "lite": false}
{"package_name": "requests", "language": "python"}
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
- **Registry APIs (PyPI, npm, crates.io, pkg.go.dev, Maven Central, NuGet):** No authentication required; generous public quotas.
- **ecosyste.ms API** (used for Maven/NuGet `get_package_info` enrichment): 5,000 requests/hour anonymous, more than enough for interactive use.
