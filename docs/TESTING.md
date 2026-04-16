# Testing

## Running Tests

```bash
pytest                              # Run all unit tests
pytest -v                           # Verbose output
pytest tests/test_scoring.py        # Specific file
pytest -m integration               # Integration tests only (require network + GITHUB_TOKEN)
pytest --cov=priorart --cov-report=html  # Coverage report
```

Integration tests are deselected in CI with `-m "not integration"`. They require network access and a valid `GITHUB_TOKEN`.

## Test Structure

```
tests/
├── conftest.py               # Shared fixtures
├── test_cache.py             # Cache backend tests
├── test_query.py             # Taxonomy matching tests
├── test_registry.py          # Registry API client tests (integration)
├── test_scoring.py           # Health score calculation tests
├── test_ast_extract.py       # AST interface extraction tests
├── test_cli.py               # CLI command tests
├── test_find_alternatives.py # Pipeline orchestration tests
├── test_ingestion.py         # Repo ingestion, injection scanning, monorepo detection
├── test_deps_dev.py          # deps.dev pure-logic (version parsing, release CV, dep info)
└── test_github_client.py     # URL parsing, identity verification (mocked PyGithub)
```

## Fixtures (conftest.py)

- `temp_cache_dir` — temporary directory for cache tests
- `mock_github_token` — sets a fake `GITHUB_TOKEN` env var
- `sample_package_snapshot` — `SignalSnapshot` for cache tests
- `sample_package_data` — dict of package signals for scoring tests
- `sample_config` — full config dict mirroring `config.yaml`

## Coverage

All core modules have test coverage:
- `cache.py`, `scoring.py`, `query.py`, `ast_extract.py`, `registry.py`, `cli.py`
- `find_alternatives.py`, `ingestion.py`, `deps_dev.py`, `github_client.py`

Not yet covered:
- `server.py` (thin MCP wrapper, `# pragma: no cover`)

## CI

Tests run on push/PR via `.github/workflows/test.yml` across Python 3.10, 3.11, 3.12. Integration tests are excluded in CI (`-m "not integration"`). Coverage uploads to Codecov (best-effort, non-blocking).
