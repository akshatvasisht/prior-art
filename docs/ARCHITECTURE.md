# Architecture Documentation

This document details the architectural decisions, system components, and data flow for **priorart**.

---

## Glossary

* **Health Score:** A composite 0-100 metric evaluating package quality across five weighted dimensions (reliability, adoption, versioning, activity regularity, dependency health).
* **Taxonomy:** A curated mapping from natural language task descriptions to language-specific package search queries.
* **Registry-First:** Architecture pattern prioritizing package registry APIs (PyPI, npm, etc.) over GitHub API to minimize rate limits and improve signal quality.
* **Signal Group:** Collection of related metadata with independent cache freshness windows (e.g., downloads, repository metadata, MTTR).
* **Ingestion:** Process of cloning a repository, prioritizing relevant files, and extracting interface definitions.

## System Overview

`priorart` is a modular CLI and MCP server tool designed for deterministic package discovery and evaluation. The architecture follows a pipeline pattern:

1. **Query Resolution** - Maps task descriptions to taxonomy categories
2. **Registry Search** - Fetches candidates from package registries
3. **Signal Collection** - Gathers metadata from registries, GitHub, and deps.dev
4. **Scoring** - Computes multi-dimensional health scores
5. **Recommendation** - Returns actionable use/evaluate/build decisions

The system is stateless at the API level with local SQLite caching for performance.

## Directory Structure

```
/priorart
├── src/priorart/
│   ├── cli.py                    # Click-based CLI entry point
│   ├── server.py                 # FastMCP wrapper for MCP protocol
│   ├── core/
│   │   ├── query.py              # Taxonomy matching
│   │   ├── registry.py           # PyPI/npm/crates.io/pkg.go.dev clients
│   │   ├── github_client.py      # GitHub API with rate limiting
│   │   ├── deps_dev.py           # deps.dev API integration
│   │   ├── cache.py              # SQLite cache backend
│   │   ├── scoring.py            # Five-dimension health scorer
│   │   ├── find_alternatives.py  # Main orchestration
│   │   ├── ingest_repo.py        # Repository ingestion orchestrator
│   │   ├── ingestion.py          # Git clone + file prioritization
│   │   └── ast_extract.py        # Interface extraction (AST parsing)
│   └── data/
│       ├── config.yaml           # Tunable parameters
│       └── taxonomy.yaml         # Category definitions
├── tests/                        # Pytest test suite
├── docs/                         # Documentation
└── agentcontext/                 # Development planning artifacts
```

## Tech Stack & Decision Record

| Category | Technology | Rationale |
| :--- | :--- | :--- |
| **Language** | Python 3.10+ | Rich async ecosystem, native GitHub/registry API libraries, strong type hints |
| **CLI Framework** | Click | Declarative syntax, built-in help generation, composable commands |
| **MCP Server** | FastMCP | Official Anthropic MCP implementation, async-first, minimal boilerplate |
| **Caching** | SQLite + aiosqlite | Serverless, transactional, per-signal freshness tracking without external dependencies |
| **HTTP Client** | httpx | Async/sync dual interface, HTTP/2 support, timeout configuration |
| **GitHub API** | PyGithub | Mature library with rate limit handling and pagination |
| **AST Parsing** | ast (stdlib) | Zero external dependencies for Python interface extraction |
| **Config Format** | YAML | Human-readable, supports comments for research citations |

## Data Flow

### `find_alternatives` Flow

```
1. Input: language, task_description
2. Taxonomy Matching (query.py)
   ↓ Fail → Return structured error (no_taxonomy_match)
   ↓ Success → search_query + priority_files
3. Registry Search (registry.py)
   ↓ Check cache (7-day freshness)
   ↓ Fetch top 10 packages by downloads
4. Signal Collection (parallel)
   ├─ Registry metadata (downloads, version, license)
   ├─ GitHub signals (stars, forks, issues, commits)
   └─ deps.dev data (version history, reverse deps, vulnerabilities)
5. Scoring (scoring.py)
   ↓ Compute 5-dimension health scores
   ↓ Apply age confidence multiplier
6. Recommendation
   ↓ Score ≥75: use_existing
   ↓ Score 50-74: evaluate
   ↓ Score <50: build
7. Output: Ranked packages with scores + warnings
```

### `ingest_repo` Flow

```
1. Input: repo_url, language
2. Identity Verification (github_client.py)
   ↓ Verify repo exists and package name matches
3. Git Clone (ingestion.py)
   ↓ Shallow clone to temp directory
4. File Prioritization
   ↓ Apply taxonomy priority_files patterns
   ↓ Limit to token budget (50k tokens)
5. Interface Extraction (ast_extract.py)
   ↓ Parse AST for classes/functions/types
6. Content Assembly
   ↓ README + prioritized files + extracted interfaces
7. Output: Markdown-formatted repository content
```

## Design Constraints & Trade-offs

### Decision: Registry-First Architecture
- **Alternative Considered:** GitHub-first search (code search API, topic search)
- **Rationale:** GitHub rate limits are restrictive (30 requests/minute for code search). Registry APIs provide unlimited download-ranked results with better signal quality. GitHub is used only for secondary metadata collection after candidates are identified.

### Decision: SQLite for Caching
- **Alternative Considered:** Redis, in-memory caching
- **Rationale:** SQLite provides durability without external services. Per-signal-group freshness requires relational queries. Total cache size is small (<100MB typical), so in-memory performance is unnecessary. Portability across platforms without daemon dependencies.

### Decision: Synchronous Scoring
- **Alternative Considered:** Async scoring pipeline
- **Rationale:** Scoring is CPU-bound (mathematical operations), not I/O-bound. Parallelizing signal *collection* (network I/O) provides sufficient concurrency. Adding async complexity to scoring provides negligible performance gain and complicates determinism guarantees.

### Decision: Taxonomy Over LLM Parsing
- **Alternative Considered:** Use LLM to parse task descriptions
- **Rationale:** LLMs introduce non-determinism, latency, cost, and prompt injection risk. Taxonomy provides instant, deterministic mapping with explicit failure modes (no_taxonomy_match). Contributors can easily extend categories via YAML.

### Decision: Age Confidence Multiplier
- **Alternative Considered:** Fixed score thresholds regardless of package age
- **Rationale:** Young packages lack historical data for reliable scoring. Blending toward neutral prior (50) for packages <2 years old reduces false confidence in immature packages while allowing exceptional new packages to score moderately high.

### Decision: Monorepo Best-Effort Subdirectory Resolution
- **Alternative Considered:** Full monorepo parsing with package.json/pyproject.toml discovery
- **Rationale:** Monorepo structures are heterogeneous and require language-specific parsing. Full resolution adds significant complexity. Current approach (restrict to root docs on resolution failure) is safe and covers 95% of cases. Language-specific resolution can be added incrementally.

## Cache Freshness Windows

Signal groups have independent freshness windows based on update frequency:

| Signal Group | Freshness | Rationale |
|-------------|-----------|-----------|
| Downloads | 7 days | Weekly download counts change frequently |
| Repository Metadata | 30 days | Stars/forks change slowly over time |
| Issue MTTR | 21 days | 3-week window balances recency with sample size |
| Commit Regularity | 21 days | 3-week window captures recent activity patterns |
| Dependencies | 7 days | Vulnerabilities and deprecations require fresh data |

## Security Considerations

1. **Identity Verification:** Cross-reference package names with claimed repository URLs to detect typosquatting
2. **Prompt Injection Scanning:** Validate taxonomy queries contain concrete nouns (not arbitrary text)
3. **Parameterized Queries:** All SQL uses parameterized statements (no string concatenation)
4. **Repository Sandboxing:** Git clones execute in isolated temp directories with cleanup
5. **Token Budget Limits:** Ingestion truncates content at 50k tokens to prevent resource exhaustion

## Performance Characteristics

- **Cold start (no cache):** ~3-5s per package evaluation (parallel signal collection)
- **Warm cache:** ~50-200ms per package evaluation
- **Ingestion:** ~2-10s depending on repository size
- **Memory footprint:** ~50-100MB typical, ~500MB peak during large ingestion
- **Disk usage:** ~10-100MB cache database

## Extension Points

1. **New Languages:** Add registry client to `registry.py` + taxonomy entries
2. **New Signals:** Add collector to signal collection phase + scoring weight
3. **Custom Scoring:** Override weight configuration in `config.yaml`
4. **New Categories:** Add entries to `taxonomy.yaml` (see [TAXONOMY.md](TAXONOMY.md))
5. **AST Extractors:** Add language-specific parsers to `ast_extract.py`

## Monitoring & Observability

The system provides:
- Cache hit/miss metrics via `priorart cache-info`
- API rate limit tracking in GitHub client logs
- Structured errors with actionable hints (no_taxonomy_match, below_threshold)
- Explain mode (`--explain`) showing per-dimension score breakdowns

## Future Considerations

- **Streaming Ingestion:** Return README immediately, then stream additional files
- **Parallel Package Evaluation:** Evaluate multiple packages concurrently
- **Incremental Cache Updates:** Update stale entries in background thread
- **Language-Specific Monorepo Handling:** Full subdirectory resolution per ecosystem
- **Local LLM Fallback:** Optional local model for taxonomy matching when no confident match exists
