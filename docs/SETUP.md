# Setup Guide

Complete setup instructions for `priorart` development and deployment.

---

## Prerequisites

- **Python 3.10+** (required)
- **Git** (required)
- **GitHub Personal Access Token** (required for GitHub API access)
- **uv** (recommended) or `pip`

---

## Installation

### For End Users

#### Option 1: Using uvx (Recommended - No Installation)

```bash
# Run directly without installing
uvx priorart find --language python --task "http client"
uvx priorart-server  # For MCP server usage
```

#### Option 2: Global Installation with pip

```bash
pip install priorart

# Verify installation
priorart --version
priorart --help
```

#### Option 3: Global Installation with uv

```bash
uv pip install priorart

# Verify installation
priorart --version
```

### For Developers

#### Clone and Install in Editable Mode

```bash
# Clone the repository
git clone https://github.com/priorart/priorart
cd priorart

# Install with uv (recommended)
uv sync

# Or install with pip in editable mode
pip install -e ".[dev]"

# Verify installation
priorart --version
pytest --version
```

---

## Configuration

### 1. GitHub Token Setup (Required)

Create a GitHub Personal Access Token for API access:

1. Go to https://github.com/settings/tokens
2. Click "Generate new token (classic)"
3. Select scopes:
   - `public_repo` (read access to public repositories)
4. Generate and copy the token

#### Set the token as an environment variable:

**Linux/macOS (bash/zsh):**
```bash
export GITHUB_TOKEN="your_token_here"

# Add to ~/.bashrc or ~/.zshrc for persistence
echo 'export GITHUB_TOKEN="your_token_here"' >> ~/.bashrc
```

**Windows (PowerShell):**
```powershell
$env:GITHUB_TOKEN="your_token_here"

# For persistence, use System Properties > Environment Variables
```

**Security Best Practices:**
- Never commit tokens to version control
- Never store tokens in JSON config files
- Use environment variables or secrets managers
- Rotate tokens periodically

### 2. Cache Directory (Optional)

By default, `priorart` uses platform-specific cache directories:
- **Linux:** `~/.cache/priorart/`
- **macOS:** `~/Library/Caches/priorart/`
- **Windows:** `%LOCALAPPDATA%\priorart\cache\`

To customize the cache location:

```bash
export PRIORART_CACHE_DIR="/path/to/custom/cache"
```

### 3. Configuration Files

The system uses two YAML configuration files (no user editing required):

- **`src/priorart/data/config.yaml`** - Scoring weights, thresholds, freshness windows
- **`src/priorart/data/taxonomy.yaml`** - Category definitions and search terms

Advanced users can fork the repository to customize these files.

---

## MCP Server Setup

### Claude Desktop Configuration

Add to your Claude Desktop config file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

**Using uvx (no installation required):**
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

**Using local installation:**
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

**Security Note:** For production, use environment variable references instead of hardcoding tokens:

```json
{
  "mcpServers": {
    "priorart": {
      "command": "priorart-server"
    }
  }
}
```

Then ensure `GITHUB_TOKEN` is set in your system environment.

### Verify MCP Server

Restart Claude Desktop, then verify the server is connected:
- Look for "priorart" in the MCP servers list
- Available tools: `find_alternatives`, `ingest_repo`

---

## Development Environment Setup

### 1. Install Development Dependencies

```bash
# With uv
uv sync

# Or with pip
pip install -e ".[dev]"
```

Development dependencies include:
- `pytest` - Test runner
- `pytest-asyncio` - Async test support
- `pytest-cov` - Coverage reporting
- `ruff` - Linting and formatting
- `mypy` - Type checking
- `black` - Code formatting

### 2. Run Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=priorart --cov-report=html

# Run specific test file
pytest tests/test_scoring.py

# Run with verbose output
pytest -v
```

### 3. Code Quality Tools

```bash
# Format code with ruff
ruff format .

# Check linting rules
ruff check .

# Auto-fix linting issues
ruff check --fix .

# Type check with mypy
mypy src/priorart
```

### 4. Pre-commit Hook (Recommended)

Create `.git/hooks/pre-commit`:

```bash
#!/bin/bash
set -e

echo "Running code quality checks..."

# Format
ruff format .

# Lint
ruff check .

# Type check
mypy src/priorart

# Run tests
pytest

echo "Pre-commit checks passed"
```

Make executable:
```bash
chmod +x .git/hooks/pre-commit
```

---

## Troubleshooting

### Issue: "GitHub rate limit exceeded"

**Cause:** Missing or invalid `GITHUB_TOKEN`

**Solution:**
```bash
# Verify token is set
echo $GITHUB_TOKEN

# Check rate limit status
curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/rate_limit

# Generate new token if needed
```

### Issue: "No module named 'priorart'"

**Cause:** Package not installed or virtual environment not activated

**Solution:**
```bash
# Verify installation
pip list | grep priorart

# Reinstall if needed
pip install -e .
```

### Issue: Cache corruption or stale data

**Solution:**
```bash
# Clear cache
priorart cache-clear

# Verify cache cleared
priorart cache-info
```

### Issue: MCP server not appearing in Claude Desktop

**Solution:**
1. Check `claude_desktop_config.json` syntax (valid JSON)
2. Verify `priorart-server` command is in PATH
3. Restart Claude Desktop completely
4. Check logs: `~/Library/Logs/Claude/mcp*.log` (macOS)

### Issue: Import errors during tests

**Cause:** Missing test dependencies

**Solution:**
```bash
pip install -e ".[dev]"
```

---

## Upgrading

### Upgrade via pip

```bash
pip install --upgrade priorart
```

### Upgrade via uv

```bash
uv pip install --upgrade priorart
```

### Upgrade development installation

```bash
cd /path/to/priorart
git pull
uv sync  # or pip install -e ".[dev]"
```

---

## Uninstallation

### Remove package

```bash
pip uninstall priorart
```

### Remove cache

```bash
# Linux/macOS
rm -rf ~/.cache/priorart

# Or use the CLI command before uninstalling
priorart cache-clear
```

### Remove MCP configuration

Edit `claude_desktop_config.json` and remove the `priorart` entry.

---

## Next Steps

- Read [ARCHITECTURE.md](ARCHITECTURE.md) for system design
- Read [TESTING.md](TESTING.md) for testing guidelines
- Read [STYLE.md](STYLE.md) for coding standards
- See [API.md](API.md) for programmatic usage
- See [TAXONOMY.md](TAXONOMY.md) for contributing categories
