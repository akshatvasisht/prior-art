<p align="center">
  <img 
    width="200" 
    height="200" 
    alt="Prior Art Logo" 
    src="https://raw.githubusercontent.com/akshatvasisht/prior-art/main/docs/images/logo.png" 
  />
</p>

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![FastMCP](https://img.shields.io/badge/FastMCP-0.5%2B-009688)
![Click](https://img.shields.io/badge/CLI-Click-black)
[![PyPI](https://img.shields.io/pypi/v/priorart-agent)](https://pypi.org/project/priorart-agent/)
![License](https://img.shields.io/badge/License-MIT-green)

`priorart` is a deterministic tool for package discovery and evaluation. It retrieves candidates from a local, sigstore-verified semantic index across PyPI, npm, crates.io, pkg.go.dev, Maven Central, and NuGet — no language model at query time — scores them on signals from GitHub, deps.dev, and OpenSSF Scorecard, and returns a weighted health score with a build-or-adopt recommendation. `priorart inspect <package>` scores a single named package.

## Research inspiration

Noise-floor thresholds for registry metrics follow [Koch et al. (MADWeb 2024)](https://madweb.work/papers2024/) on the weak correlation between GitHub stars and downstream adoption. Abandonment detection follows [Coelho & Valente (ESEC/FSE 2017)](https://arxiv.org/abs/1707.02327). Adoption-saturation curves reference [Borges & Valente (JSS 2018)](https://arxiv.org/abs/1811.07643) and [Zerouali et al. (ICSR 2018)](https://arxiv.org/abs/1806.01545). Dimension taxonomy is aligned with the [CHAOSS Project](https://chaoss.community) metrics framework.

## Key properties

- **Deterministic end-to-end.** Discovery is a local HNSW query (`fastembed` + `usearch`, int8-quantized); scoring is a closed-form weighted composite. No language model at any stage — same inputs, same output.
- **Private by default.** The semantic index is a [sigstore](https://www.sigstore.dev/)-signed artifact pinned to a specific GitHub Actions signer identity. No hosted retrieval endpoint; after first-use download, queries never leave the host.
- **Reproducible.** The index is rebuilt monthly via a public GitHub Actions workflow and versioned by tag; pin a version to stabilize results across runs.
- **Calibrated scoring.** Dimension weights (0.30 / 0.20 / 0.20 / 0.15 / 0.15) follow the conventions of OpenSSF Scorecard, npms.io, and SourceRank. Not empirically validated across ecosystems; override in `config.yaml`.
- **Supply-chain signals.** Identity verification, copyleft detection, dependency-vulnerability flags, and OpenSSF Scorecard checks feed the composite score.

## Pipeline

1. **Semantic retrieval.** Task description is embedded with `BAAI/bge-small-en-v1.5` and queried against a per-ecosystem HNSW index. Falls back to live registry search when top similarity < 0.5.
2. **Signal collection.** Registry metadata, GitHub repository metrics, deps.dev graphs, and OpenSSF Scorecard results; cached in SQLite with per-signal-group freshness windows.
3. **Scoring.** Weighted composite across reliability, adoption, versioning, activity regularity, and dependency health, with an age-based confidence multiplier for packages under three years.
4. **Recommendation.** `use_existing` (≥ 75), `evaluate` (50–74), or `build` (< 50).

## Install

```bash
pip install priorart-agent
```

## Documentation

- **[SETUP.md](docs/SETUP.md)** — installation, environment, and MCP server setup.
- **[API.md](docs/API.md)** — CLI, Python API, and MCP tool reference.
- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)** — scoring algorithm, data flow, and cache design.
- **[STYLE.md](docs/STYLE.md)** — coding standards.
- **[TESTING.md](docs/TESTING.md)** — test organization and coverage.
- **[AGENT_CONFIG.md](docs/AGENT_CONFIG.md)** — guidance for AI agents invoking the MCP tools.

## License

See [LICENSE](LICENSE) for details.

Package metadata in the distributed semantic index is sourced from [ecosyste.ms](https://ecosyste.ms) and licensed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Redistributing the index shard preserves that license.
