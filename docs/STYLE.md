# Coding Standards & Style Guide

## General Principles

### Professionalism & Tone

* All comments and documentation use objective, technical language
* Avoid informal language or environment-specific justifications
* **Correct:** "Defaults to in-memory cache for faster CI execution"
* **Incorrect:** "Using memory cache because my laptop was slow"

### Intent over Implementation

* Comments explain *why* decisions were made, not *what* code does
* Code should be self-documenting for the *what*
* **Correct:** `# Blend toward neutral prior for packages <2 years old`
* **Incorrect:** `# This line calculates the score`

### No Meta-Commentary

* No internal debate traces, failed attempt logs, or editing notes in committed code
* **Correct:** `# Use staggered requests to respect GitHub secondary rate limits`
* **Incorrect:** `# Tried parallel requests but kept hitting rate limits`

---

## Python Guidelines

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Variables | `snake_case` | `health_score`, `repo_url` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_RESULTS`, `CACHE_TTL` |
| Functions | `snake_case` | `calculate_score()`, `fetch_repo()` |
| Classes | `PascalCase` | `CacheBackend`, `RegistryClient` |
| Private/Internal | `_leading_underscore` | `_compute_signals()` |
| Modules | `snake_case` | `ast_extract.py`, `github_client.py` |

### Type Annotations

Use type hints for all function signatures:

```python
# Good
def calculate_score(signals: dict[str, float]) -> dict[str, Any]:
    ...

# Bad
def calculate_score(signals):
    ...
```

For complex types, use `typing` module:

```python
from typing import Optional, Union, Literal

def fetch_repo(
    url: str,
    branch: Optional[str] = None
) -> dict[str, Union[str, int]]:
    ...
```

### Async/Await

- Use `async`/`await` for I/O-bound operations (network, file system)
- Avoid `async` for CPU-bound operations (scoring, parsing)
- Use `asyncio.gather()` for parallel async operations

```python
# Good: Parallel I/O
results = await asyncio.gather(
    fetch_repo_metadata(repo),
    fetch_download_stats(package),
    fetch_deps_data(package)
)

# Bad: Sequential when parallel is possible
metadata = await fetch_repo_metadata(repo)
downloads = await fetch_download_stats(package)
deps = await fetch_deps_data(package)
```

### Error Handling

- Use specific exception types
- Fail fast with structured errors
- Include actionable hints in error messages

```python
# Good
if not concrete_noun_in_query(query):
    raise ValueError(
        "Could not confidently map task description. "
        "Hint: Pass a concrete noun describing the capability (e.g., 'http client', 'parser')."
    )

# Bad
if not valid_query(query):
    raise Exception("Invalid query")
```

### Docstrings

Use Google-style docstrings for public APIs:

```python
def calculate_health_score(signals: dict[str, float]) -> dict[str, Any]:
    """Calculate multi-dimensional health score for a package.

    Args:
        signals: Dictionary of package signals (downloads, stars, commits, etc.)

    Returns:
        Dictionary containing:
            - health_score: 0-100 composite score
            - recommendation: "use_existing" | "evaluate" | "build"
            - dimension_scores: Per-dimension breakdown

    Raises:
        ValueError: If required signals are missing
    """
    ...
```

---

## Code Formatting

### Automated Tools

Use `ruff` for linting and formatting:

```bash
# Format code
ruff format .

# Check linting
ruff check .

# Auto-fix issues
ruff check --fix .
```

Configuration in `pyproject.toml`:
```toml
[tool.ruff]
line-length = 100
target-version = "py310"
select = ["E", "F", "I", "N", "W", "B", "UP"]
ignore = ["E501", "B008", "B905"]
```

### Line Length

- Maximum 100 characters
- Break long lines at logical boundaries

```python
# Good
result = calculate_health_score(
    signals=package_signals,
    weights=scoring_weights,
    age_days=package_age
)

# Bad
result = calculate_health_score(signals=package_signals, weights=scoring_weights, age_days=package_age)
```

### Imports

Group imports in order:
1. Standard library
2. Third-party packages
3. Local application imports

Use `ruff` to auto-sort with `isort` rules:

```python
# Good
import asyncio
import json
from typing import Optional

import httpx
from github import Github

from priorart.core.cache import CacheBackend
from priorart.core.utils import parse_repo_url
```

---

## Git Workflow

### Branch Naming

Follow systematic naming:

| Type | Pattern | Example |
|------|---------|---------|
| Feature | `feature/description` | `feature/add-rust-support` |
| Bug Fix | `fix/description` | `fix/cache-corruption` |
| Chore | `chore/description` | `chore/update-deps` |
| Docs | `docs/description` | `docs/api-examples` |

### Commit Messages

Use imperative mood, present tense:

```
# Good
Add rate limiting to GitHub API client
Fix cache expiration logic for deps.dev signals
Update taxonomy with graphql_client category

# Bad
Added rate limiting
Fixed bug
Updates
```

Format:
```
<type>: <subject>

<optional body>

<optional footer>
```

Types: `feat`, `fix`, `docs`, `chore`, `test`, `refactor`

### Pull Requests

PRs must include:
- **Description:** What changed and why
- **Testing:** How changes were tested
- **Breaking Changes:** If any, clearly documented

---

## Code Comments

### When to Comment

**Comment:**
- Non-obvious algorithms or math
- Performance optimizations
- Workarounds for external API limitations
- Security considerations
- Research-backed decisions

**Don't Comment:**
- Obvious code (e.g., `x = 1  # Set x to 1`)
- Redundant docstrings
- Commented-out code (use git history instead)

### Examples

```python
# Good: Explains why, references research
# Use Koch et al. MADWeb 2024 threshold of 350 weekly downloads
# to filter noise packages before scoring
if downloads_per_week < 350:
    continue

# Good: Explains non-obvious behavior
# Blend toward neutral prior (50) for packages <2 years old
# to reduce false confidence in immature packages
confidence = min(1.0, age_days / 730)
score = (raw_score * confidence) + (50 * (1 - confidence))

# Bad: States the obvious
# Loop through packages
for pkg in packages:
    ...

# Bad: Meta-commentary
# TODO: This is hacky, need to refactor
# Tried using X but it didn't work
```

---

## Testing Standards

See [TESTING.md](TESTING.md) for comprehensive testing guidelines.

Quick reference:
- Test file names: `test_<module>.py`
- Test function names: `test_<feature>_<scenario>`
- Use fixtures for common setup
- Mock external APIs
- Aim for ≥80% coverage

---

## Security Considerations

### Secrets Management

- Never hardcode secrets in code
- Use environment variables
- No secrets in config files or git history

```python
# Good
github_token = os.environ.get("GITHUB_TOKEN")
if not github_token:
    raise ValueError("GITHUB_TOKEN environment variable required")

# Bad
github_token = "ghp_xyz123..."
```

### SQL Injection Prevention

Always use parameterized queries:

```python
# Good
cursor.execute("SELECT * FROM cache WHERE key = ?", (key,))

# Bad
cursor.execute(f"SELECT * FROM cache WHERE key = '{key}'")
```

### Input Validation

Validate all external inputs:

```python
# Good
if not re.match(r'^[a-z0-9\-]+$', package_name):
    raise ValueError(f"Invalid package name: {package_name}")

# Bad
query_db(package_name)  # No validation
```

---

## File Organization

### Module Structure

```python
"""Module docstring explaining purpose."""

# Standard library imports
import os
from typing import Optional

# Third-party imports
import httpx

# Local imports
from priorart.core.cache import CacheBackend

# Constants
MAX_RESULTS = 10
DEFAULT_TTL = 3600

# Classes
class MyClass:
    ...

# Functions
def my_function():
    ...

# Main execution (if script)
if __name__ == "__main__":
    ...
```

---

## Performance Considerations

- Profile before optimizing
- Cache expensive operations
- Use generators for large datasets
- Prefer `asyncio` for I/O parallelism

```python
# Good: Generator for large results
def iter_packages(query: str):
    for page in range(10):
        yield from fetch_page(query, page)

# Bad: Load everything into memory
def get_packages(query: str):
    results = []
    for page in range(10):
        results.extend(fetch_page(query, page))
    return results
```

---

## Configuration

Configuration lives in `src/priorart/data/config.yaml`:

- Include research citations for magic numbers
- Use clear, descriptive keys
- Group related settings

```yaml
# Good
floor_filter:
  min_weekly_downloads: 350  # Koch et al. MADWeb 2024
  min_stars: 50

# Bad
filter:
  x: 350  # some threshold
  y: 50
```

---

## Additional Resources

- [PEP 8](https://peps.python.org/pep-0008/) - Python style guide
- [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)
- [ARCHITECTURE.md](ARCHITECTURE.md) - System design
- [TESTING.md](TESTING.md) - Testing guidelines
