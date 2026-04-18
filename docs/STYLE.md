# Coding Standards

## General Principles

### Tone

Use objective, technical language. Avoid informal phrasing and environment-specific justifications.

- **Correct:** "Defaults to in-memory cache for faster CI execution."
- **Incorrect:** "Using memory cache because my laptop was slow."

### Intent over implementation

Comments explain *why*, not *what* — identifiers carry the *what*.

- **Correct:** `# Blend toward neutral prior for packages <2 years old`
- **Incorrect:** `# This line calculates the score`

### No meta-commentary

No debate traces, failed-attempt logs, or editing notes in committed code.

- **Correct:** `# Stagger requests to respect GitHub secondary rate limits`
- **Incorrect:** `# Tried parallel requests but kept hitting rate limits`

---

## Code Comments

### When to Comment

**Comment:**
- Non-obvious algorithms or math
- Performance optimizations
- Workarounds for external API limitations
- Security considerations
- Research-backed decisions

**Don't Comment:**
- Obvious code (e.g., `x = 1  # Set x to 1`)
- Redundant docstrings
- Commented-out code (use git history instead)

### Examples

```python
# Good: Explains why, references research
# Use Koch et al. MADWeb 2024 threshold of 350 weekly downloads
# to filter noise packages before scoring
if weekly_downloads < 350:
    continue

# Good: Explains non-obvious behavior
# Blend toward neutral prior (50) for packages <2 years old
# to reduce false confidence in immature packages
confidence = min(1.0, age_days / 730)
score = (raw_score * confidence) + (50 * (1 - confidence))

# Bad: States the obvious
# Loop through packages
for pkg in packages:
    ...

# Bad: Meta-commentary
# TODO: This is hacky, need to refactor
# Tried using X but it didn't work
```

---

## Tooling

Linting and formatting are handled by `ruff`. See `[tool.ruff]` in `pyproject.toml` for the current configuration.

```bash
ruff format .       # Format code
ruff check .        # Check linting
ruff check --fix .  # Auto-fix issues
```

For all other conventions (naming, imports, type annotations, docstrings), follow [PEP 8](https://peps.python.org/pep-0008/) and the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html). Ruff enforces import sorting and most formatting rules automatically.
