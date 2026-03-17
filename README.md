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
[![Tests](https://github.com/priorart/priorart/actions/workflows/test.yml/badge.svg)](https://github.com/priorart/priorart/actions/workflows/test.yml)
[![Lint](https://github.com/priorart/priorart/actions/workflows/lint.yml/badge.svg)](https://github.com/priorart/priorart/actions/workflows/lint.yml)

priorart is a build-vs-borrow intelligence system for determining whether to implement custom infrastructure or adopt open-source packages. It provides deterministic package discovery and multi-dimensional health evaluation based on quantitative metrics and empirical research.

### Research Basis

priorart's evaluation engine is informed by empirical software engineering research on package abandonment, maintenance sustainability, and noise floors in registry data.

- **Threshold Modeling**: Noise floors for registry data (downloads/stars) derived from **Koch et al. (MADWeb 2024)**.
- **Abandonment Detection**: Inactivity and dormancy thresholds based on **Coelho et al. (2017)**.
- **Adoption Signals**: Committer and reverse-dependency saturation metrics informed by **Borges et al. (2018)** and **Zerouali et al. (2018)**.
- **Sustainability Metrics**: Health indicators aligned with the **CHAOSS Project** (Community Health Analytics in Open Source Software).

### How It Works

priorart implements a 5-layer evaluation pipeline:

1.  **Taxonomy Mapping**: Maps natural language task descriptions to curated registry search queries.
2.  **Registry Discovery**: Fetches top candidates from package registries (PyPI, npm, crates.io, pkg.go.dev) ranked by download count.
3.  **Signal Collection**: Enriches candidates with GitHub repository signals (stars, forks, MTTR, commit regularity) and deps.dev health data.
4.  **Multidimensional Scoring**: Computes weighted scores across reliability, adoption, versioning, activity regularity, and dependency health.
5.  **Decision Classification**: Categorizes packages into `use_existing` (score ≥75), `evaluate` (50-74), or `build` (< 50).

### Impact & Performance

- **Precision Discovery**: Registry-first architecture skips GitHub search noise, focusing only on viable candidates.
- **High Performance**: Evaluation completes in 50-200ms (cached) or 3-5s (cold).
- **Deterministic Evaluation**: Replaces non-deterministic LLM recommendations with verifiable quantitative metrics.
- **Security-First**: Integrated checks for typosquatting (identity verification), copyleft licenses, and dependency vulnerabilities.

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
