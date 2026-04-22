# Architecture

## Glossary

- **Health score.** Composite 0–100 metric across five weighted dimensions: reliability, adoption, versioning, activity regularity, and dependency health.
- **Semantic index.** Per-ecosystem HNSW shard (`usearch`) of package name and description embeddings, hosted on Hugging Face Hub and downloaded on first use.
- **Signal group.** A set of related metadata fields sharing a single cache-freshness window.
- **Build-cost enrichment.** Output fields (`build_cost_weeks`, `commodity_tag`, `maintenance_liability`) derived from the composite score and registry metadata.

## Directory layout

```
src/priorart/
├── cli.py                    # Click CLI entry point
├── server.py                 # FastMCP adapter
├── core/
│   ├── retrieval.py          # Semantic HNSW query + registry fallback
│   ├── index_download.py     # Hugging Face Hub download + sigstore verification
│   ├── registry.py           # PyPI, npm, crates.io, pkg.go.dev, Maven Central, NuGet clients
│   ├── github_client.py      # GitHub API with rate-limit handling
│   ├── deps_dev.py           # deps.dev client
│   ├── scorecard_client.py   # OpenSSF Scorecard client
│   ├── cache.py              # Pooled SQLite cache
│   ├── scoring.py            # Five-dimension scorer
│   ├── build_cost.py         # Build-cost enrichment
│   ├── find_alternatives.py  # Pipeline orchestration
│   ├── inspect.py            # Single-package evaluation
│   ├── ingest_repo.py        # Ingestion orchestrator
│   ├── ingestion.py          # Git clone and file prioritization
│   └── ast_extract.py        # Interface extraction (AST + regex)
└── data/config.yaml          # Tunable parameters
```

## Tech stack

| Component | Library | Rationale |
|---|---|---|
| Language | Python 3.10+ | Typed, mature registry and GitHub client libraries |
| CLI | Click | Declarative argument parsing |
| MCP server | FastMCP | Reference MCP server implementation |
| Cache | `sqlite3` (stdlib) | Embedded, transactional, no daemon |
| HTTP | `httpx` | Sync and async interfaces, HTTP/2 |
| GitHub API | PyGithub | Pagination and rate-limit handling |
| Embeddings | `fastembed` | Quantized ONNX inference, no GPU required |
| Vector index | `usearch` | Int8-quantized HNSW, compact shards |
| AST | `ast` (stdlib) | Zero dependency for Python extraction |

## Data flow

### `find_alternatives`

```
1. Input: language, task_description
2. Semantic retrieval (retrieval.py)
   ├─ Embed query with BAAI/bge-small-en-v1.5
   ├─ L2-normalize and int8-quantize
   ├─ usearch HNSW cosine query against ecosystem shard
   └─ Fall back to live registry search when top similarity < 0.5
3. Signal collection
   ├─ Registry metadata (downloads, version, license)
   ├─ GitHub (stars, forks, issue MTTR, commit regularity)
   ├─ deps.dev (version graph, reverse deps, vulnerabilities)
   └─ OpenSSF Scorecard (reliability and dependency-health checks)
4. Scoring (scoring.py)
   └─ Weighted composite with age-confidence multiplier
5. Build-cost enrichment (build_cost.py)
   └─ Engineer-weeks estimate, commodity tag, maintenance-liability flag
6. Output: top N ranked packages with scores and warnings
```

### `ingest_repo`

```
1. Input: repo_url, optional language
2. Shallow git clone to temporary directory
3. Language-aware file prioritization
4. Content budget applied (default 24,000 characters)
5. Interface extraction via AST (Python) or regex (JS, TS, Rust, Go)
6. Output: Markdown-formatted interface summary
```

## Scoring

```
raw_score      = 0.30·reliability + 0.20·adoption + 0.20·versioning
               + 0.15·activity_regularity + 0.15·dependency_health

confidence     = min(age_years / 3.0, 1.0)
health_score   = (confidence · raw_score + (1 − confidence) · 0.5) · 100
```

Packages are classified as `use_existing` (≥ 75), `evaluate` (50–74), or `build` (< 50). All weights, thresholds, and saturation constants are defined in `src/priorart/data/config.yaml` and validated at startup.

## Design decisions

| Decision | Alternative | Rationale |
|---|---|---|
| Registry-first discovery | GitHub code or topic search | GitHub secondary rate limits throttle concurrent expensive calls; registries expose native download-rank ordering with no authentication. |
| SQLite cache | Redis or in-memory | Embedded, durable, no external service. |
| Synchronous scoring | Async pipeline | Scoring is CPU-bound; async adds complexity without throughput benefit. |
| Semantic index | LLM query parsing | Offline after initial download, no per-query token cost, deterministic, no prompt-injection surface. |
| Pinned sigstore signer identity | Unsigned shards | A compromised Hugging Face repository cannot poison retrieval; the signer is pinned to `.github/workflows/rebuild-index.yml@refs/heads/main`. |
| Int8-quantized embeddings | float32 | Approximately 4× shard-size reduction with negligible recall loss on unit vectors. |
| Age-confidence multiplier | Fixed thresholds | Young packages lack operational history; blend toward a neutral prior (50) under three years. |
| Top ~20,000 packages per ecosystem | Full registry ingestion | Long-tail inactivity (53% of 2025 crates never updated, 57% of PyPI had no release in 12 months); the cutoff matches OpenSSF Criticality Score. Outside packages are served via live registry search. |

Weights (0.30 / 0.20 / 0.20 / 0.15 / 0.15) are calibrated expert judgment, matching the conventions of OpenSSF Scorecard, npms.io, and SourceRank. Cross-ecosystem validity is not established ([Xia et al., EMSE 2022](https://link.springer.com/article/10.1007/s10664-022-10144-3)); override in `config.yaml`. Sub-dimension citations are in the README.

## Cache freshness

Signal groups have independent freshness windows defined in `config.yaml`:

| Signal group | Window | Rationale |
|---|---|---|
| Downloads | 7 days | Weekly counts change frequently |
| Repository metadata | 30 days | Stars and forks change slowly |
| Issue MTTR | 21 days | Balances recency and sample size |
| Commit regularity | 21 days | Captures recent activity patterns |
| Dependencies | 7 days | Vulnerability data must be fresh |

Per-group staleness detection (`cache.py:is_signal_group_stale`) exists but is not yet wired into the collection pipeline; any cached snapshot is served regardless of age.

## Security

- **Identity verification.** Package-name and repository-URL cross-reference to detect typosquatting.
- **Prompt-injection scanning.** Regex patterns loaded from `config.yaml` and applied during ingestion.
- **Parameterized SQL.** All queries use parameter binding; dynamic column names are validated against an allowlist.
- **Path protection.** Ingestion skips symlinks and validates that resolved paths remain within the clone directory.
- **Submodule isolation.** Git clones use `--no-recurse-submodules` to prevent SSRF via `.gitmodules`.
- **Content budget.** Ingestion truncates at 24,000 characters by default to bound resource usage.
