# Taxonomy Contribution Guide

The taxonomy maps natural language task descriptions to curated package search queries. Stored in `src/priorart/data/taxonomy.yaml`.

## Category Schema

```yaml
categories:
  - id: unique_category_id         # Lowercase with underscores
    keywords: [list, of, keywords] # For task description matching
    search_terms:                  # Registry search queries
      python: "python-specific search"
      javascript: "js-specific search"
      typescript: "ts-specific search"
      go: "go-specific search"
      rust: "rust-specific search"
      default: "fallback search"   # Required
    priority_files:                # Optional: file patterns for ingestion
      python: ["*.pyi", "*.py"]
      typescript: ["*.d.ts", "*.ts"]
      default: ["README*", "*.md"]
    service_note: "Optional note"  # Optional: managed service alternatives
```

## Field Definitions

### `id` (required)
Unique lowercase identifier with underscores. Examples: `http_client`, `database`, `rate_limiter`.

### `keywords` (required)
Keywords and phrases for matching user task descriptions. Include primary terms, common variations, related concepts, acronyms, and common misspellings. Use lowercase.

```yaml
keywords: [http, request, fetch, rest, api client, curl, web request]
```

### `search_terms` (required)
Language-specific search queries for package registries. Keep queries short (2-4 words). Use terms that appear in package names. Always provide a `default` fallback.

```yaml
search_terms:
  python: "http client requests"
  javascript: "http client fetch axios"
  rust: "http client reqwest"
  default: "http client"
```

### `priority_files` (optional)
File patterns to prioritize during repository ingestion. List in priority order. Include type definitions (`.pyi`, `.d.ts`) and entry points.

### `service_note` (optional)
Note about managed service alternatives. Include when the category is commonly solved by managed services with significant advantages (reliability, scalability, compliance). Omit for purely client-side categories (parsers, formatters).

## Adding a New Category

### 1. Research
Verify: packages exist in multiple ecosystems, task is general-purpose, clear keywords exist, not already covered.

### 2. Define
Create your category following the schema above.

### 3. Test

```bash
pip install -e .
priorart find --language python --task "your test query"
```

### 4. Submit PR
Branch: `taxonomy/your-category`. Include test results and list of discovered packages.

## Existing Categories

See `src/priorart/data/taxonomy.yaml` for the full list of currently defined categories and their keywords.
