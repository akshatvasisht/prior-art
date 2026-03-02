![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![FastMCP](https://img.shields.io/badge/FastMCP-0.5%2B-009688)
![Click](https://img.shields.io/badge/CLI-Click-black)
![License](https://img.shields.io/badge/License-MIT-green)

priorart is a build-vs-borrow intelligence system designed to help AI agents make informed decisions about whether to build custom solutions or use existing open source packages. Instead of relying on LLM package recommendations that often miss quality signals or suggest abandoned projects, priorart provides deterministic package discovery and multi-dimensional health evaluation based on quantitative metrics.

### Architectural Approach

AI agents frequently reinvent infrastructure that already exists in mature packages, or conversely adopt packages with hidden maintenance risks. priorart addresses this by implementing a registry-first discovery architecture combined with a five-dimension health scoring system. Rather than querying GitHub's rate-limited search API, the system searches package registries (PyPI, npm, crates.io, pkg.go.dev) for download-ranked results, then enriches candidates with GitHub signals and dependency health data. This hybrid approach provides comprehensive package evaluation while respecting API rate limits.

### How priorart Works

priorart operates as an end-to-end evaluation pipeline:

1. **Taxonomy Mapping Layer**
   Maps natural language task descriptions to curated registry search queries via a taxonomy of common package categories.
2. **Registry Discovery Layer**
   Fetches top candidates from package registries ranked by download count, avoiding GitHub rate limits.
3. **Signal Collection Layer**
   Gathers metadata from three sources: registry APIs (downloads, versions, license), GitHub (stars, forks, issues, commits), and deps.dev (dependency health, reverse dependencies).
4. **Five-Dimension Scoring Layer**
   Computes weighted health scores across reliability, adoption, versioning, activity regularity, and dependency health. Scores are adjusted by an age confidence multiplier to reduce false confidence in young packages.
5. **Recommendation Layer**
   Classifies packages as use_existing (score ≥75), evaluate (50-74), or build (< 50) with actionable guidance.

## Performance

- **Evaluation Speed**: 50-200ms per package (cached), 3-5s per package (cold)
- **Cache Efficiency**: Per-signal-group freshness tracking with configurable TTLs (7-30 days)
- **API Rate Limits**: Registry-first architecture minimizes GitHub API usage (5,000 req/hour with token)
- **Determinism**: Same inputs always produce same outputs (no LLM non-determinism)

### Applications

**AI Agent Workflows**
- Reduces redundant implementation by surfacing existing packages before agents start coding infrastructure.

**Package Due Diligence**
- Provides quantitative health metrics for evaluating packages before adoption in production systems.

**Open Source Discovery**
- Surfaces high-quality packages across ecosystems (Python, JavaScript/TypeScript, Rust, Go) using consistent evaluation criteria.

## Installation

```bash
# Using uvx (no installation required)
uvx priorart find --language python --task "http client"

# Or install globally
pip install priorart
```

### CLI Usage

```bash
# Find packages for a task
priorart find --language python --task "http client"

# With detailed scoring breakdown
priorart find -l javascript -t "rate limiter" --explain

# Ingest repository to understand interface
priorart ingest https://github.com/psf/requests -l python

# JSON output for scripting
priorart find -l rust -t "json parser" --json
```

### MCP Server Usage

Add to Claude Desktop config:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "priorart": {
      "command": "uvx",
      "args": ["priorart-server"],
      "env": {
        "GITHUB_TOKEN": "your_token_here"
      }
    }
  }
}
```

### Python API Usage

```python
from priorart import find_alternatives

result = find_alternatives(
    language="python",
    task_description="http client",
    explain=True
)

for pkg in result['packages']:
    print(f"{pkg['name']}: {pkg['health_score']}/100 - {pkg['recommendation']}")
```

## Configuration

Environment variables:

- `GITHUB_TOKEN` (required) - GitHub Personal Access Token for API access
  - Create at: https://github.com/settings/tokens
  - Required scopes: `public_repo` (read-only)
- `PRIORART_CACHE_DIR` (optional) - Custom cache directory

Cache management:

```bash
priorart cache-info   # View cache statistics
priorart cache-clear  # Clear cache
```

Scoring weights and thresholds are configurable in `src/priorart/data/config.yaml`:

```yaml
weights:
  reliability: 0.30
  adoption: 0.20
  versioning: 0.20
  activity_regularity: 0.15
  dependency_health: 0.15

floor_filter:
  min_weekly_downloads: 350  # Koch et al. MADWeb 2024
  min_stars: 50

recommendation:
  use_existing_min: 75
  evaluate_min: 50
```

## Supported Languages

- Python (PyPI)
- JavaScript/TypeScript (npm)
- Rust (crates.io)
- Go (pkg.go.dev)

## Contributing

### Adding Taxonomy Categories

Edit `src/priorart/data/taxonomy.yaml`:

```yaml
categories:
  - id: category_name
    keywords: [keyword1, keyword2]
    search_terms:
      python: "python specific search"
      default: "general search"
    priority_files:
      python: ["*.pyi", "*.py"]
```

See `TAXONOMY.md` for contribution guidelines.

### Development Setup

```bash
git clone https://github.com/priorart/priorart
cd priorart

# Install with uv (recommended)
uv sync

# Or install in editable mode
pip install -e ".[dev]"

# Run tests
pytest

# Format code
ruff format .
ruff check .
```

## Documentation

- **[SETUP.md](docs/SETUP.md)**: Installation, configuration, and MCP setup
- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)**: System design, data flow, and design decisions
- **[API.md](docs/API.md)**: Python API, CLI, and MCP server reference
- **[TESTING.md](docs/TESTING.md)**: Testing guidelines
- **[STYLE.md](docs/STYLE.md)**: Coding standards
- **[AGENT_CONFIG.md](docs/AGENT_CONFIG.md)**: Agent usage guidelines
- **[TAXONOMY.md](docs/TAXONOMY.md)**: Taxonomy contribution guide

## License

See **[LICENSE](LICENSE)** file for details.

## Acknowledgments

Scoring methodology informed by research from Koch et al. (MADWeb 2024), Coelho et al. (2017), Borges et al. (2018), Zerouali et al. (2018), and the CHAOSS Project.
