# Setup Guide

## Prerequisites

- **Python 3.10+**
- **Git**
- **GitHub Personal Access Token** (required for GitHub API access)

---

## Installation

```bash
# From PyPI (published as `priorart-agent`; imports and CLI are still `priorart`)
pip install priorart-agent

# Or from source with uv
git clone https://github.com/akshatvasisht/prior-art
cd prior-art
uv sync

# Or editable install with pip
pip install -e ".[dev]"

# Verify
priorart --version
```

---

## GitHub Token Setup

Create a GitHub Personal Access Token for API access:

1. Go to https://github.com/settings/tokens
2. Click "Generate new token (classic)"
3. Select scope: `public_repo`
4. Generate and copy the token

```bash
export GITHUB_TOKEN="your_token_here"

# Add to ~/.bashrc or ~/.zshrc for persistence
echo 'export GITHUB_TOKEN="your_token_here"' >> ~/.bashrc
```

Without `GITHUB_TOKEN`, GitHub signals are skipped and packages score with registry/deps.dev data only.

---

## MCP Server Setup

Add to your Claude Desktop config:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "priorart": {
      "command": "priorart-server",
      "env": {
        "GITHUB_TOKEN": "your_token_here"
      }
    }
  }
}
```

Restart Claude Desktop. Tools `find_alternatives` and `ingest_repo` should appear in the MCP servers list.

---

## Cache Directory

Cache is stored at the platform-specific location via `platformdirs`:
- **Linux:** `~/.cache/priorart/`
- **macOS:** `~/Library/Caches/priorart/`
- **Windows:** `%LOCALAPPDATA%\priorart\cache\`

---

## Development

```bash
# Format + lint
ruff format .
ruff check --fix .

# Tests
pytest
pytest --cov=priorart --cov-report=html

# Type check
mypy src/priorart
```

Dev dependencies: `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `mypy`

---

## Troubleshooting

### "GitHub rate limit exceeded"
Verify `GITHUB_TOKEN` is set: `echo $GITHUB_TOKEN`

### "No module named 'priorart'"
Reinstall: `pip install -e .`

### Cache corruption
Clear cache: `priorart cache-clear`

### MCP server not appearing
1. Check `claude_desktop_config.json` syntax
2. Verify `priorart-server` is in PATH
3. Restart Claude Desktop
4. Check logs: `~/Library/Logs/Claude/mcp*.log` (macOS)
