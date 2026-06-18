# CLAUDE.md

## What this project is

`priorart` — a build-vs-borrow intelligence tool for AI agents. Given a task description and language, it discovers and health-scores open source packages to help decide whether to use an existing library or build from scratch.

Three tools exposed via MCP and CLI:
- `find_alternatives` — semantic retrieval (hosted HNSW shard) → signal collection → 5-dimensional scoring → top 5 packages, with build-vs-borrow lens
- `evaluate_package` / `priorart inspect` — score a single named package, skipping retrieval
- `ingest_repo` — clone a candidate repo and extract its public API surface

## Environment setup

```bash
# Required for GitHub signal collection
export GITHUB_TOKEN="your_token_here"

# Install in editable mode
pip install -e ".[dev]"

# Or with uv
uv sync
```

Without `GITHUB_TOKEN`, GitHub signals are skipped and packages score with only registry/deps.dev data.

## Running things

```bash
# CLI
priorart find --language python --task "http client"
priorart find --language python --task "http client" --explain
priorart inspect requests --language python
priorart cache-info
priorart cache-clear
priorart ingest https://github.com/psf/requests
priorart ingest https://github.com/psf/requests --language python

# MCP server
priorart-server

# Tests
pytest
pytest tests/test_scoring.py -v
pytest --cov=priorart
```

## Architecture

Pipeline in `src/priorart/core/`:

```
task_description + language
        ↓
  retrieval.py        — embed (fastembed) + HNSW query (usearch) against per-ecosystem shard
  index_download.py   — first-use download + sigstore verification of the shard manifest
                        (falls back to live registry search when similarity < 0.5)
        ↓
  github_client.py    — stars, forks, MTTR, commit regularity (cold cache only)
  deps_dev.py         — version graph, dep health, reverse deps (cold cache only)
  scorecard_client.py — OpenSSF Scorecard reliability + dep-health buckets
  cache.py            — pooled SQLite snapshot at ~/.cache/priorart/cache.db
        ↓
  scoring.py          — 5-dimension weighted score + age confidence multiplier
  build_cost.py       — engineer-weeks, commodity tag, maintenance liability
        ↓
  find_alternatives.py  — orchestrates the full pipeline
  inspect.py            — same scoring path, single named package, no retrieval
```

CLI entry: `cli.py` → calls core functions directly
MCP entry: `server.py` → thin FastMCP wrapper calling same functions

Config lives in `src/priorart/data/config.yaml`. Top-level dimension weights, thresholds, and freshness windows are there. Sub-dimension weights within each scoring function (e.g. adoption's `0.35 * dl_score`) and saturation constants (e.g. `dl_saturation = 10_000_000`) are hardcoded in `scoring.py`.

## Key files

| File | What it does |
|---|---|
| `core/find_alternatives.py` | Main orchestration — read this to understand the full flow |
| `core/scoring.py` | 5-dimension scoring engine + age confidence multiplier |
| `core/cache.py` | SQLite cache with per-signal-group freshness windows |
| `core/github_client.py` | GitHub API: MTTR, commit CV, FSR, identity verification |
| `core/registry.py` | PyPI, npm, crates.io, pkg.go.dev search clients |
| `core/deps_dev.py` | deps.dev API: version graphs, dep health, reverse dep count |
| `core/scorecard_client.py` | OpenSSF Scorecard client — reliability + dep-health bucket scores |
| `core/retrieval.py` | Semantic HNSW retrieval; falls back to live registry search below similarity 0.5 |
| `core/index_download.py` | First-use HF Hub download + sigstore verification of the shard manifest |
| `core/build_cost.py` | Build-vs-borrow enrichment (engineer-weeks, commodity tag, maintenance liability) |
| `core/inspect.py` | Single-package scoring (skips retrieval) used by `priorart inspect` and `evaluate_package` |
| `core/ingestion.py` | Git clone + two-pass file prioritization + injection scanning |
| `core/ast_extract.py` | Python AST + regex interface extraction (also TS, JS, Rust, Go via regex) |
| `scripts/index_build/` | Offline pipeline (ecosyste.ms S3 dump → slim JSONL on HF Hub → fastembed → usearch → sigstore → HF Hub) |
| `.github/workflows/rebuild-index.yml` | Monthly GH Actions rebuild; identity-pinned sigstore signer |
| `.github/workflows/extract-snapshot.yml` | Weekly probe + on-change streaming extract of the ecosyste.ms PostgreSQL dump into `priorart/package-snapshot` |
| `bench/` | Retrieval benchmark harness (condensed nDCG@10 / Success@5). **Gold is awesome-list-derived and misaligned with the product goal — a regression smoke-test, not a quality signal; don't tune retrieval against it. Overhaul plan: `agentcontext/OPEN_ISSUES.md` A23.** |
| `data/config.yaml` | All tunable parameters with research citations |

## Known gotchas

**`GitHubSignals` has no `fork_to_star_ratio` field.** The ratio is computed from `fork_count` and `star_count` in `find_alternatives._fetch_fresh_signals`. Do not add it to `GitHubSignals`.

**All datetimes must be timezone-aware (UTC).** PyGithub returns timezone-aware datetimes. Mixing naive and aware datetimes raises `TypeError` at runtime. Always use `datetime.now(timezone.utc)`, never `datetime.utcnow()`. Cache reads normalize stored naive strings with `.replace(tzinfo=timezone.utc)`.

**`PackageCandidate.__eq__` is based on `name` + `registry` only**, not all fields. Intentional for deduplication.

**Injection patterns are loaded from `config.yaml`** (`security.prompt_injection_patterns`) at `RepositoryIngester` init time, with `re.escape()` applied. `config.yaml` is the source of truth. The fallback (if config is unavailable) is an inline literal list in `__init__`. To add or change patterns, edit `config.yaml`.

**`dl_score` in adoption scoring uses log-normalized `weekly_downloads`**, not a percentile. Saturation is hardcoded at 10M/week. PyPI weekly downloads are now fetched from pypistats.org per package (optional, fails silently). For packages with no download data, `dl_score` is 0 and the floor filter falls back to star count.

**`RegistryClient` and `DepsDevClient` are context managers.** Always use `with get_registry_client(language) as client:` or `with DepsDevClient() as client:` to avoid httpx connection pool leaks.

**PyPI `author` field is often `None`** for packages using PEP 621 metadata. The `get_package_info` method falls back to parsing `author_email` and `maintainer_email` (format: `"Name <email>"`).

**Semantic index is hosted, not bundled.** `index_download.ensure_shard(ecosystem)` lazily fetches from the HF Hub dataset `priorart/package-index` into `~/.cache/priorart/index/`. The manifest is sigstore-verified against the pinned `rebuild-index.yml@refs/heads/main` signer identity. Set `PRIORART_INDEX_URL` to point at a mirror, or `PRIORART_INDEX_DIR` to relocate the cache (useful for tests).

**`SQLiteCache` is now pooled.** Pass `pool_size=N` to size the connection queue; the default of 4 is sized for the MCP server. Connections use `check_same_thread=False` because the pool hands each one out to a single caller at a time; SQLite itself serializes writes.

## agentcontext/

Local-only dev artifacts (gitignored). Not committed. Read these before starting significant work:

- `PRD.md` — canonical requirements and all design rationale. Read this for the "why" behind any scoring formula or architectural choice.
- `DECISIONS.md` — key architectural decisions and known limitations.
- `OPEN_ISSUES.md` — confirmed bugs, deferred refactors, and future direction (including rearchitecture plan).
- `BUILD_LOG.md` — archival record of what was built and what was simplified. Stale on specifics.
- `BENCHMARK.md` — retrieval + scoring benchmark methodology for `bench/`: BEIR harness, nDCG@10 / Recall@5 / MRR, awesome-lists + Stack Overflow ground truth, registry-search / BM25 / libraries.io baselines.

## Scoring quick reference

```
raw_score = 0.30 * reliability + 0.20 * adoption + 0.20 * versioning
          + 0.15 * activity_regularity + 0.15 * dependency_health

health_score = (confidence * raw_score + (1 - confidence) * 0.5) * 100
  where confidence = min(age_years / 3.0, 1.0)

use_existing: score >= 75
evaluate:     score 50-74
build:        score < 50
```

Top-level dimension weights and recommendation thresholds are in `data/config.yaml`. Validated at startup — bad weights raise immediately.

**`days_since_compatible_release` is populated from deps.dev** — `_fetch_fresh_signals` in `core/find_alternatives.py` resolves the publish date of `latest_version` via `_latest_stable_published_at(deps_data)` and sets the signal. Falls back to the 365-day default only when the publish date is missing.

**Semantic index covers Go** — the old hardcoded `pkg.go.dev` package dictionary was superseded by the HNSW shard built from the ecosyste.ms snapshot. The registry client still exists as a fallback for `--lite` mode but is no longer the primary discovery path.
