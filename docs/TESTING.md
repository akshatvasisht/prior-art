# Testing

## Running tests

```bash
pytest                                   # Unit tests
pytest -v                                # Verbose output
pytest tests/test_scoring.py             # Single file
pytest -m integration                    # Integration tests (require network and GITHUB_TOKEN)
pytest --cov=priorart --cov-report=html  # Coverage report
```

Integration tests are excluded in CI via `-m "not integration"` and require network access and a valid `GITHUB_TOKEN`.

## Layout

```
tests/
├── conftest.py                    # Shared fixtures
├── test_ast_extract.py            # AST and regex interface extraction
├── test_bench_gold_standard.py    # Awesome-list gold-standard parser
├── test_bench_metrics.py          # nDCG, Recall@k, MRR
├── test_build_cost.py             # Build-cost enrichment
├── test_cache.py                  # Pooled SQLite cache
├── test_cli.py                    # Click command surface
├── test_deps_dev.py               # deps.dev client
├── test_find_alternatives.py      # Pipeline orchestration
├── test_github_client.py          # GitHub client and identity verification
├── test_index_build_fetch.py      # BigQuery fetcher dedup and fallback
├── test_index_download.py         # Hugging Face Hub download and sigstore verification
├── test_ingest_repo.py            # Ingestion entry point
├── test_ingestion.py              # Clone, injection scanning, monorepo detection
├── test_inspect.py                # Single-package evaluation
├── test_registry.py               # Registry clients (PyPI, npm, crates.io, pkg.go.dev)
├── test_retrieval.py              # Semantic retrieval and registry fallback
├── test_scorecard_client.py       # OpenSSF Scorecard client
└── test_scoring.py                # Five-dimension scorer
```

## Fixtures (`conftest.py`)

- `temp_cache_dir` — isolated cache directory.
- `mock_github_token` — sets a placeholder `GITHUB_TOKEN`.
- `sample_package_snapshot` — `SignalSnapshot` for cache tests.
- `sample_package_data` — signal dict for scoring tests.
- `sample_config` — configuration dict matching `config.yaml`.

## Coverage

Line coverage is 100% across `src/priorart/`. The sigstore verification body in `index_download.py` is marked `# pragma: no cover` as an external cryptographic boundary exercised only in integration.

## CI

Tests run on push and pull request via `.github/workflows/test.yml` on Python 3.10, 3.11, and 3.12. Integration tests are excluded (`-m "not integration"`). Coverage is uploaded to Codecov as a non-blocking step.
