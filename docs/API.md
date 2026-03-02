# API Documentation

Complete API reference for programmatic usage of `priorart`.

---

## Python API

### Installation

```python
pip install priorart
```

### Core Functions

#### `find_alternatives()`

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
| `language` | `str` | Yes | Target language: `"python"`, `"javascript"`, `"typescript"`, `"rust"`, `"go"` |
| `task_description` | `str` | Yes | Natural language task description (e.g., "http client", "rate limiter") |
| `explain` | `bool` | No | Include per-dimension score breakdowns (default: `False`) |

**Returns:** `dict`

```python
{
    "status": "success",
    "packages": [
        {
            "name": "requests",
            "health_score": 82,
            "recommendation": "use_existing",  # "use_existing" | "evaluate" | "build"
            "url": "https://github.com/psf/requests",
            "description": "Python HTTP library...",
            "downloads_per_week": 50000000,
            "stars": 51000,
            "license": "Apache-2.0",
            "license_warning": False,
            "identity_verified": True,
            "likely_abandoned": False,
            "dep_health_flag": False,
            "dimension_scores": {  # Only if explain=True
                "reliability": 85,
                "adoption": 95,
                "versioning": 80,
                "activity_regularity": 75,
                "dependency_health": 90
            }
        },
        # ... more packages
    ],
    "service_note": None  # Optional string suggesting managed alternatives
}
```

**Error Responses:**

```python
# No taxonomy match
{
    "status": "no_taxonomy_match",
    "message": "Could not confidently map task description...",
    "hint": "Pass a concrete noun describing the capability..."
}

# No results found
{
    "status": "no_results",
    "message": "No packages found matching 'obscure query'"
}

# All candidates below quality threshold
{
    "status": "below_threshold",
    "message": "All candidates were below minimum download/star thresholds"
}
```

**Example:**

```python
from priorart import find_alternatives

# Basic usage
result = find_alternatives(
    language="python",
    task_description="http client"
)

for pkg in result["packages"]:
    print(f"{pkg['name']}: {pkg['health_score']}/100 - {pkg['recommendation']}")

# With explanations
result = find_alternatives(
    language="javascript",
    task_description="rate limiter",
    explain=True
)

top_pkg = result["packages"][0]
print(f"Top package: {top_pkg['name']}")
print(f"Dimension scores: {top_pkg['dimension_scores']}")
```

---

#### `ingest_repo()`

Clone a repository and extract interface documentation.

```python
from priorart import ingest_repo

content = ingest_repo(
    repo_url="https://github.com/psf/requests",
    language="python"
)
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `repo_url` | `str` | Yes | GitHub repository URL (https://github.com/owner/repo) |
| `language` | `str` | Yes | Language for file prioritization: `"python"`, `"javascript"`, `"typescript"`, `"rust"`, `"go"` |

**Returns:** `dict`

```python
{
    "status": "success",
    "content": "# Repository: requests\n\n## README\n...\n\n## Key Files\n...",
    "identity_verified": True,
    "files_included": ["README.md", "requests/__init__.py", "requests/api.py"],
    "token_count": 12500
}
```

**Error Responses:**

```python
# Repository not found
{
    "status": "error",
    "message": "Repository not found: https://github.com/invalid/repo"
}

# Identity verification failed
{
    "status": "error",
    "message": "Package identity could not be verified",
    "identity_verified": False
}
```

**Example:**

```python
from priorart import ingest_repo

# Ingest repository
result = ingest_repo(
    repo_url="https://github.com/encode/httpx",
    language="python"
)

if result["status"] == "success":
    print(f"Extracted {len(result['files_included'])} files")
    print(f"Token count: {result['token_count']}")
    print(result["content"][:500])  # Preview content
```

---

### Cache Management

#### Clear Cache

```python
from priorart.core.cache import clear_cache

clear_cache()
print("Cache cleared")
```

#### Get Cache Info

```python
from priorart.core.cache import get_cache_info

info = get_cache_info()
print(f"Cache size: {info['size_mb']} MB")
print(f"Entry count: {info['entry_count']}")
print(f"Location: {info['path']}")
```

---

## CLI Reference

### `priorart find`

Find and evaluate packages.

```bash
priorart find --language LANG --task "DESCRIPTION" [OPTIONS]
```

**Options:**

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--language` | `-l` | Target language | Required |
| `--task` | `-t` | Task description | Required |
| `--explain` | | Show dimension scores | `false` |
| `--json` | | Output JSON | `false` |

**Examples:**

```bash
# Basic usage
priorart find -l python -t "http client"

# With explanations
priorart find -l javascript -t "rate limiter" --explain

# JSON output for scripting
priorart find -l rust -t "json parser" --json
```

---

### `priorart ingest`

Ingest repository content.

```bash
priorart ingest REPO_URL [OPTIONS]
```

**Options:**

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--language` | `-l` | Target language | Required |
| `--output` | `-o` | Output file path | stdout |

**Examples:**

```bash
# Print to stdout
priorart ingest https://github.com/psf/requests -l python

# Save to file
priorart ingest https://github.com/encode/httpx -l python -o httpx_docs.md
```

---

### `priorart cache-info`

View cache information.

```bash
priorart cache-info
```

**Output:**

```
Cache Statistics
================
Location: /home/user/.cache/priorart/
Size: 45.2 MB
Entries: 237
Oldest entry: 2024-02-15
Newest entry: 2024-03-02
```

---

### `priorart cache-clear`

Clear the cache.

```bash
priorart cache-clear
```

**Output:**

```
Cache cleared successfully
Location: /home/user/.cache/priorart/
```

---

## MCP Server

### Tools

The MCP server exposes two tools via the Model Context Protocol.

#### `find_alternatives`

**Input Schema:**

```json
{
  "language": "python",
  "task_description": "http client",
  "explain": false
}
```

**Output:**

Same as Python API `find_alternatives()` return value.

---

#### `ingest_repo`

**Input Schema:**

```json
{
  "repo_url": "https://github.com/psf/requests",
  "language": "python"
}
```

**Output:**

Same as Python API `ingest_repo()` return value.

---

## Configuration

### Environment Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `GITHUB_TOKEN` | GitHub Personal Access Token | Yes | None |
| `PRIORART_CACHE_DIR` | Custom cache directory | No | Platform-specific |

### Config Files

**Location:** `src/priorart/data/config.yaml`

**Customization:**

Fork the repository and modify `config.yaml`:

```yaml
# Scoring dimension weights (must sum to 1.0)
weights:
  reliability: 0.30
  adoption: 0.20
  versioning: 0.20
  activity_regularity: 0.15
  dependency_health: 0.15

# Minimum thresholds for candidates
floor_filter:
  min_weekly_downloads: 350  # Koch et al. MADWeb 2024
  min_stars: 50

# Recommendation score thresholds
recommendation:
  use_existing_min: 75
  evaluate_min: 50

# Cache freshness windows (days)
cache_freshness:
  downloads: 7
  repo_metadata: 30
  issue_mttr: 21
  commit_regularity: 21
  dependencies: 7
```

---

## Error Handling

### Exception Types

All functions may raise:

- `ValueError` - Invalid input parameters
- `RuntimeError` - External API failures
- `EnvironmentError` - Missing required environment variables

### Example Error Handling

```python
from priorart import find_alternatives

try:
    result = find_alternatives(
        language="python",
        task_description="http client"
    )

    if result["status"] == "success":
        # Process packages
        for pkg in result["packages"]:
            print(pkg["name"])

    elif result["status"] == "no_taxonomy_match":
        print(f"Error: {result['message']}")
        print(f"Hint: {result['hint']}")

except ValueError as e:
    print(f"Invalid input: {e}")

except RuntimeError as e:
    print(f"API error: {e}")

except EnvironmentError as e:
    print(f"Configuration error: {e}")
```

---

## Rate Limits

### GitHub API

- **Limit:** 5,000 requests/hour with authentication
- **Strategy:** Registry-first approach minimizes GitHub API usage
- **Fallback:** Gracefully degrades if rate limit exceeded

### Registry APIs

- **PyPI, npm, crates.io:** No rate limits (unauthenticated access)
- **pkg.go.dev:** No rate limits (unauthenticated access)

---

## Additional Resources

- [SETUP.md](SETUP.md) - Installation and configuration
- [ARCHITECTURE.md](ARCHITECTURE.md) - System design
- [AGENT_CONFIG.md](AGENT_CONFIG.md) - Agent usage guidelines
- [TAXONOMY.md](TAXONOMY.md) - Contributing categories
