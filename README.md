<p align="center">
  <img 
    width="200" 
    height="200" 
    alt="Prior Art Logo" 
    src="docs/images/logo.png" 
  />
</p>

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![FastMCP](https://img.shields.io/badge/FastMCP-0.5%2B-009688)
![Click](https://img.shields.io/badge/CLI-Click-black)
![License](https://img.shields.io/badge/License-MIT-green)

priorart is a deterministic package evaluation tool for build-vs-borrow decisions. Given a natural language task description and target language, it queries package registries directly, collects quantitative signals from GitHub and deps.dev, and produces a scored recommendation based on configurable, research-informed heuristics.

### Research Inspiration

Noise-floor thresholds for registry metrics are informed by **[Koch et al. (MADWeb 2024)](https://madweb.work/papers2024/)**, which quantified the weak, language-dependent correlation between GitHub stars and downstream adoption. Abandonment detection follows **[Coelho & Valente (ESEC/FSE 2017)](https://arxiv.org/abs/1707.02327)** on categorizing open-source project failure modes. Adoption saturation curves for committer diversity and reverse-dependency counts reference **[Borges & Valente (JSS 2018)](https://arxiv.org/abs/1811.07643)** and **[Zerouali et al. (ICSR 2018)](https://arxiv.org/abs/1806.01545)** on technical lag in dependency networks. Health dimensions are aligned with the **[CHAOSS Project](https://chaoss.community)** metrics framework.

### Pipeline

1.  **Taxonomy Mapping** — Maps task descriptions to curated, language-specific registry search queries.
2.  **Registry Discovery** — Fetches candidates from PyPI, npm, crates.io, or pkg.go.dev, ranked by download count.
3.  **Signal Collection** — Enriches each candidate with GitHub repository metrics (stars, forks, MTTR, commit regularity) and deps.dev dependency health data.
4.  **Multidimensional Scoring** — Computes weighted scores across reliability, adoption, versioning, activity regularity, and dependency health.
5.  **Decision Classification** — Classifies packages as `use_existing` (≥75), `evaluate` (50–74), or `build` (<50).

### Properties

- **Registry-first discovery** — Queries registries directly; does not rely on GitHub search.
- **Latency** — 50–200 ms cached, 3–5 s cold.
- **Deterministic** — Scoring is fully quantitative; no LLM-generated recommendations.
- **Supply-chain checks** — Identity verification (typosquatting), copyleft license detection, and dependency vulnerability flags.

## Documentation

- **[SETUP.md](docs/SETUP.md)**: Installation, environment configuration, and MCP server setup.
- **[API.md](docs/API.md)**: Comprehensive guide to the CLI, Python API, and MCP tool definitions.
- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)**: Deep dive into the scoring algorithms, data flow, and cache design.
- **[STYLE.md](docs/STYLE.md)**: Project coding standards and architectural invariants.
- **[TESTING.md](docs/TESTING.md)**: Guidelines for running unit and integration tests.
- **[AGENT_CONFIG.md](docs/AGENT_CONFIG.md)**: Specific protocols for AI agents using priorart in autonomous workflows.
- **[TAXONOMY.md](docs/TAXONOMY.md)**: Guide for contributing new package categories and search terms.

## License

See **[LICENSE](LICENSE)** file for details.
