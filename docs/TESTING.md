# Testing Guidelines

## Strategy

`priorart` uses `pytest` for automated testing with async support via `pytest-asyncio`. The test suite prioritizes determinism, isolation, and fast execution.

### Test Types

* **Unit Tests:** Test individual functions (scoring, query matching, AST extraction) in isolation with mocked dependencies
* **Integration Tests:** Verify interactions between components (cache + registry, GitHub client + rate limiter)
* **End-to-End Tests:** Validate full workflows (`find_alternatives`, `ingest_repo`) with real external APIs (marked `@pytest.mark.integration`)

## Running Tests

### Full Test Suite

```bash
# Run all tests (excluding integration tests by default)
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_scoring.py

# Run specific test function
pytest tests/test_scoring.py::test_health_score_calculation
```

### Integration Tests

Integration tests require a valid `GITHUB_TOKEN` environment variable:

```bash
# Run integration tests only
pytest -m integration

# Run all tests including integration
pytest -m ""
```

### Coverage Reporting

```bash
# Generate coverage report
pytest --cov=priorart --cov-report=html

# View report
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

### Watch Mode (Development)

```bash
# Install pytest-watch
pip install pytest-watch

# Run tests on file changes
ptw
```

## Test Structure

```
tests/
├── __init__.py
├── conftest.py              # Shared fixtures
├── test_cache.py            # Cache backend tests
├── test_query.py            # Taxonomy matching tests
├── test_registry.py         # Registry API client tests
├── test_scoring.py          # Health score calculation tests
├── test_ast_extract.py      # AST interface extraction tests
├── test_cli.py              # CLI command tests
└── README.md                # Test documentation
```

## Writing New Tests

### Arrange / Act / Assert Pattern

```python
def test_health_score_calculation():
    # Arrange: Set up test data
    signals = {
        "downloads_per_week": 10000,
        "stars": 500,
        "forks": 50,
        # ...
    }

    # Act: Execute the function under test
    result = calculate_health_score(signals)

    # Assert: Verify expected behavior
    assert result["health_score"] >= 0
    assert result["health_score"] <= 100
    assert result["recommendation"] in ["use_existing", "evaluate", "build"]
```

### Test Isolation

- **No shared mutable state** between tests
- **Use fixtures** for common setup (see `conftest.py`)
- **Mock external APIs** to avoid rate limits and ensure determinism
- **Use `tmp_path` fixture** for file system operations

Example:
```python
@pytest.fixture
def mock_github_client(monkeypatch):
    """Mock GitHub API responses"""
    mock_repo = MagicMock()
    mock_repo.stargazers_count = 1000
    mock_repo.forks_count = 100
    monkeypatch.setattr("priorart.core.github_client.get_repo", lambda _: mock_repo)
    return mock_repo

def test_with_mocked_github(mock_github_client):
    # Test uses mocked GitHub client
    result = fetch_repo_signals("user/repo")
    assert result["stars"] == 1000
```

### Async Test Support

Use `@pytest.mark.asyncio` for async functions:

```python
import pytest

@pytest.mark.asyncio
async def test_async_cache_get():
    cache = CacheBackend(":memory:")
    await cache.set("key", "value", ttl=60)
    result = await cache.get("key")
    assert result == "value"
```

### Naming Conventions

- Test files: `test_<module>.py`
- Test functions: `test_<feature>_<scenario>`
- Test classes: `Test<Feature>` (use for grouping related tests)

Examples:
- `test_scoring_with_missing_signals()`
- `test_query_matching_no_taxonomy_match()`
- `test_cache_expiration_behavior()`

## Mocking Standards

### External APIs

Mock external API calls to ensure tests are fast and deterministic:

```python
from unittest.mock import MagicMock, patch

@patch("priorart.core.registry.httpx.get")
def test_pypi_search(mock_get):
    mock_get.return_value.json.return_value = {
        "results": [{"name": "requests", "downloads": 10000}]
    }
    results = search_pypi("http client")
    assert len(results) > 0
```

### Database/Cache

Use in-memory SQLite for cache tests:

```python
@pytest.fixture
async def memory_cache():
    """In-memory cache for testing"""
    cache = CacheBackend(":memory:")
    await cache.initialize()
    yield cache
    await cache.close()
```

### File System

Use `tmp_path` fixture for file operations:

```python
def test_repo_ingestion(tmp_path):
    repo_dir = tmp_path / "test-repo"
    repo_dir.mkdir()
    (repo_dir / "README.md").write_text("# Test")

    result = prioritize_files(str(repo_dir), ["*.md"])
    assert "README.md" in result
```

## Test Fixtures

Common fixtures are defined in `conftest.py`:

- `mock_github_token` - Provides fake GitHub token
- `memory_cache` - In-memory cache backend
- `sample_taxonomy` - Example taxonomy data
- `sample_signals` - Example package signals

Use fixtures to reduce boilerplate:

```python
def test_with_fixtures(memory_cache, sample_signals):
    # Use pre-configured fixtures
    score = calculate_health_score(sample_signals)
    assert score > 0
```

## Troubleshooting Tests

### Import Errors

**Issue:** `ImportError: cannot import name 'module'`

**Fix:**
```bash
# Ensure package is installed in editable mode
pip install -e .

# Verify PYTHONPATH includes src/
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"
```

### Async Test Failures

**Issue:** `RuntimeError: Event loop is closed`

**Fix:** Ensure `pytest-asyncio` is installed and tests use `@pytest.mark.asyncio`:
```bash
pip install pytest-asyncio
```

### Mocking Failures

**Issue:** `AttributeError: Mock object has no attribute 'X'`

**Fix:** Configure mock return values explicitly:
```python
mock_obj = MagicMock()
mock_obj.method.return_value = expected_value
```

### Integration Test Failures

**Issue:** `GitHub rate limit exceeded`

**Fix:** Set `GITHUB_TOKEN` environment variable with valid token:
```bash
export GITHUB_TOKEN="your_token_here"
pytest -m integration
```

### Test Isolation Failures

**Issue:** Tests pass individually but fail when run together

**Fix:** Check for shared global state or improperly scoped fixtures:
```python
# BAD: Module-level state
cache = CacheBackend()

# GOOD: Fixture-scoped state
@pytest.fixture
def cache():
    return CacheBackend(":memory:")
```

## Continuous Integration

Tests run automatically on pull requests via GitHub Actions (see `.github/workflows/test.yml`):

- Python 3.10, 3.11, 3.12 compatibility
- Unit tests on all commits
- Integration tests on main branch only
- Coverage reporting to Codecov

## Test Coverage Goals

- **Core modules:** ≥90% coverage (scoring, query, registry, cache)
- **CLI/Server:** ≥70% coverage (UI/IO heavy code)
- **Overall:** ≥80% coverage

Check current coverage:
```bash
pytest --cov=priorart --cov-report=term-missing
```

## Performance Benchmarking

Use `pytest-benchmark` for performance-critical code:

```python
def test_scoring_performance(benchmark):
    signals = generate_sample_signals()
    result = benchmark(calculate_health_score, signals)
    assert result["health_score"] > 0
```

Run benchmarks:
```bash
pip install pytest-benchmark
pytest tests/test_scoring.py --benchmark-only
```

## Test Modules

### Unit Tests (Fast, No Network)

Tests run quickly without network access:

- `test_cache.py` - Cache backend functionality
- `test_scoring.py` - Scoring engine logic
- `test_query.py` - Taxonomy mapping
- `test_ast_extract.py` - Code interface extraction
- `test_cli.py` - CLI interface

### Integration Tests (Require Network)

Tests making real API calls to external services:

- `test_registry.py` - Registry API clients (PyPI, npm, crates.io)

Run integration tests only:
```bash
pytest -m integration
```

**Note**: Integration tests require internet connectivity and may be affected by rate limiting, service availability, and network conditions.

## Current Coverage

Coverage by module:

- `cache.py` - Full coverage of cache operations
- `scoring.py` - Complete scoring engine tests
- `query.py` - Taxonomy mapping and confidence scoring
- `ast_extract.py` - Interface extraction for all languages
- `registry.py` - Registry client tests (integration)
- `cli.py` - CLI command tests

**Not yet covered** (future work):
- `deps_dev.py` - deps.dev API integration
- `github_client.py` - GitHub API integration
- `ingestion.py` - Repository cloning and ingestion
- `find_alternatives.py` - End-to-end orchestration
- `ingest_repo.py` - Repository analysis tool

## Known Limitations

1. **Integration tests require network** - May fail in offline environments
2. **Rate limiting** - GitHub/registry APIs may rate limit during frequent test runs
3. **External dependencies** - Tests depend on external service availability
4. **Time-sensitive** - Some tests involve datetime calculations affected by execution time
