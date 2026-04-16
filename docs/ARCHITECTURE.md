# Architecture

## Glossary

* **Health Score:** Composite 0-100 metric across five weighted dimensions (reliability, adoption, versioning, activity regularity, dependency health).
* **Taxonomy:** Curated mapping from natural language task descriptions to language-specific package search queries.
* **Registry-First:** Architecture pattern prioritizing package registry APIs over GitHub API to minimize rate limits.
* **Signal Group:** Collection of related metadata with independent cache freshness windows.

## System Overview

`priorart` is a modular CLI and MCP server tool for deterministic package discovery and evaluation. Pipeline pattern:

1. **Query Resolution** — Maps task descriptions to taxonomy categories
2. **Registry Search** — Fetches candidates from package registries
3. **Signal Collection** — Gathers metadata from registries, GitHub, and deps.dev
4. **Scoring** — Computes multi-dimensional health scores
5. **Recommendation** — Returns actionable use/evaluate/build decisions

Stateless at the API level with local SQLite caching for performance.

## Directory Structure

```
/prior-art
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
│   │   └── ast_extract.py        # Interface extraction (AST + regex)
│   └── data/
│       ├── config.yaml           # Tunable parameters
│       └── taxonomy.yaml         # Category definitions
├── tests/
├── docs/
└── agentcontext/                 # Dev planning artifacts (gitignored)
```

## Tech Stack

| Category | Technology | Rationale |
| :--- | :--- | :--- |
| **Language** | Python 3.10+ | Rich ecosystem, native GitHub/registry API libraries, strong type hints |
| **CLI** | Click | Declarative syntax, built-in help generation |
| **MCP Server** | FastMCP | Official Anthropic MCP implementation |
| **Caching** | sqlite3 (stdlib) | Serverless, transactional, no external dependencies |
| **HTTP Client** | httpx | Async/sync dual interface, HTTP/2 support |
| **GitHub API** | PyGithub | Mature library with rate limit handling and pagination |
| **AST Parsing** | ast (stdlib) | Zero dependencies for Python interface extraction |
| **Config** | YAML | Human-readable, supports comments for research citations |

## Data Flow

### `find_alternatives`

```
1. Input: language, task_description
2. Taxonomy Matching (query.py)
   ↓ Fail → Return no_taxonomy_match
   ↓ Success → search_query + priority_files
3. Registry Search (registry.py)
   ↓ Fetch top candidates by downloads
4. Signal Collection
   ├─ Registry metadata (downloads, version, license)
   ├─ GitHub signals (stars, forks, issues, commits)
   └─ deps.dev data (version history, reverse deps, vulnerabilities)
5. Scoring (scoring.py)
   ↓ 5-dimension health scores + age confidence multiplier
6. Output: Top 5 ranked packages with scores + warnings
```

### `ingest_repo`

```
1. Input: repo_url, language (optional)
2. Git Clone → shallow clone to temp directory
3. File Prioritization → taxonomy priority_files patterns
4. Content Budget → limit to 24,000 characters
5. Interface Extraction → AST parsing for public API surface
6. Output: Markdown-formatted repository content
```

## Design Decisions

| Decision | Alternative Considered | Rationale |
|---|---|---|
| Registry-first search | GitHub code/topic search | GitHub rate limits (30 req/min for code search). Registries provide unlimited download-ranked results. |
| SQLite caching | Redis, in-memory | Durability without external services. Portable, no daemon. |
| Synchronous scoring | Async pipeline | Scoring is CPU-bound. Async adds complexity without performance gain. |
| Taxonomy over LLM | LLM parsing | Determinism, no latency/cost, explicit failure modes, no prompt injection risk. |
| Age confidence multiplier | Fixed thresholds | Young packages lack history. Blend toward neutral (50) for <3 years reduces false confidence. |

## Cache Freshness

Signal groups have independent freshness windows defined in `config.yaml`:

| Signal Group | Window | Rationale |
|-------------|--------|-----------|
| Downloads | 7 days | Weekly counts change frequently |
| Repository Metadata | 30 days | Stars/forks change slowly |
| Issue MTTR | 21 days | Balances recency with sample size |
| Commit Regularity | 21 days | Captures recent activity patterns |
| Dependencies | 7 days | Vulnerabilities require fresh data |

**Note:** Per-group staleness checking is defined in `cache.py` (`is_signal_group_stale`) but not yet wired into the collection pipeline. Currently, any cached snapshot is used regardless of age.

## Security

1. **Identity Verification** — Cross-reference package names with repository URLs to detect typosquatting
2. **Prompt Injection Scanning** — Patterns loaded from `config.yaml`, applied during ingestion
3. **Parameterized SQL** — All queries use parameter binding; dynamic column names validated against allowlist
4. **Symlink Protection** — Ingestion skips symlinks and validates paths stay within clone directory
5. **Submodule Isolation** — Git clones use `--no-recurse-submodules` to prevent SSRF via `.gitmodules`
6. **Content Budget** — Ingestion truncates at 24,000 characters to prevent resource exhaustion
