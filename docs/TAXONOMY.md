# Taxonomy Contribution Guide

The taxonomy maps natural language task descriptions to curated package search queries. This document explains how to contribute new categories.

## Overview

The taxonomy is stored in `src/priorart/data/taxonomy.yaml` and contains categories for common package types across languages. Each category defines:

- Keywords for matching task descriptions
- Language-specific search terms for registry queries
- Priority file patterns for repository ingestion
- Optional service notes about managed alternatives

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

Unique identifier for the category. Use lowercase with underscores.

**Examples:**
- `http_client`
- `database`
- `rate_limiter`

### `keywords` (required)

List of keywords and phrases for matching user task descriptions. Include:
- Primary term (e.g., "http", "database")
- Common variations (e.g., "request", "fetch", "curl")
- Related concepts (e.g., "rest", "api client")

**Guidelines:**
- Use lowercase
- Include plurals and singulars
- Add acronyms (e.g., "jwt", "oauth")
- Include common misspellings if frequent

**Example:**
```yaml
keywords: [
  http, request, fetch, rest, api client, curl,
  web request, http get, http post
]
```

### `search_terms` (required)

Language-specific search queries for package registries.

**Guidelines:**
- Keep queries short (2-4 words)
- Use terms that appear in package names
- Include popular package names when appropriate
- Always provide a `default` fallback

**Example:**
```yaml
search_terms:
  python: "http client requests"
  javascript: "http client fetch axios"
  typescript: "http client fetch axios"
  rust: "http client reqwest"
  go: "http client"
  default: "http client"
```

### `priority_files` (optional)

File patterns to prioritize during repository ingestion, per language.

**Guidelines:**
- List patterns in priority order
- Include type definitions (`.pyi`, `.d.ts`)
- Include entry points (`__init__.py`, `index.ts`)
- Always provide `default` fallback

**Example:**
```yaml
priority_files:
  python: ["*.pyi", "src/**/*.py", "__init__.py"]
  typescript: ["*.d.ts", "src/**/*.ts", "index.ts"]
  rust: ["src/lib.rs", "src/main.rs"]
  default: ["README*", "*.md"]
```

### `service_note` (optional)

Note about managed service alternatives for this category.

**When to include:**
- Category is commonly solved by managed services
- Managed services offer significant advantages (reliability, scalability, compliance)

**When NOT to include:**
- Category is purely client-side (parsers, formatters, validators)
- Managed services don't offer clear advantages

**Format:**
```yaml
service_note: |
  [Category] is commonly handled by managed [service type]
  ([Service1], [Service2], [Service3]). Consider evaluating
  managed alternatives alongside these open source results.
```

**Example:**
```yaml
service_note: |
  Authentication is commonly solved by managed identity services
  (Auth0, Okta, AWS Cognito, Firebase Auth). Consider evaluating
  managed alternatives alongside these open source results.
```

## Adding a New Category

### 1. Research

Before adding a category, verify:
- Packages exist in multiple ecosystems
- Task is general-purpose (not domain-specific)
- Clear keywords for matching
- Not already covered by existing category

### 2. Define Schema

Create your category following the schema above. Example:

```yaml
- id: graphql_client
  keywords: [
    graphql, graph ql, gql, graphql client,
    graphql query, apollo client
  ]
  search_terms:
    python: "graphql client gql"
    javascript: "graphql apollo client"
    typescript: "graphql apollo client"
    rust: "graphql client"
    go: "graphql client"
    default: "graphql client"
  priority_files:
    python: ["*.pyi", "*.py"]
    typescript: ["*.d.ts", "*.ts"]
    default: ["README*", "*.md"]
  service_note: null
```

### 3. Test Locally

Test your category:

```bash
# Install in editable mode
pip install -e .

# Test taxonomy matching
priorart find --language python --task "your test query"

# Verify it matches your new category
# Check that returned packages are relevant
```

### 4. Submit PR

1. Fork the repository
2. Create a branch: `git checkout -b taxonomy/your-category`
3. Add your category to `src/priorart/data/taxonomy.yaml`
4. Test thoroughly
5. Submit PR with description:
   - What category you added
   - Why it's needed
   - Test results showing it works

**PR Template:**
```markdown
## New Taxonomy Category: [category_id]

### Description
[Brief description of what this category covers]

### Test Results
```
priorart find --language python --task "[your query]"
# Paste relevant output showing successful match
```

### Packages Found
- Package 1 (score: XX)
- Package 2 (score: XX)
- Package 3 (score: XX)

### Checklist
- [ ] Category ID is unique
- [ ] Keywords cover common variations
- [ ] Search terms tested in each language
- [ ] Priority files specified (if applicable)
- [ ] Service note added (if applicable)
- [ ] Locally tested and working
```

## Common Categories (Reference)

### Client Libraries
- `http_client` - HTTP/REST clients
- `websocket` - WebSocket clients
- `graphql_client` - GraphQL clients
- `grpc_client` - gRPC clients

### Data & Storage
- `database` - Database ORMs/clients
- `cache` - Caching libraries
- `queue` - Message queues
- `search` - Full-text search

### Authentication & Security
- `authentication` - Auth libraries
- `authorization` - Permission systems
- `crypto` - Cryptography
- `jwt` - JWT handling

### Parsing & Validation
- `parser` - Generic parsers
- `json_parser` - JSON parsing
- `xml_parser` - XML parsing
- `validation` - Data validation

### Infrastructure
- `logging` - Logging libraries
- `monitoring` - Metrics/monitoring
- `testing` - Testing frameworks
- `cli` - CLI frameworks

### Utilities
- `datetime` - Date/time handling
- `email` - Email sending
- `file_handling` - File operations
- `rate_limiter` - Rate limiting

## Guidelines Summary

**DO:**
- Add categories for general-purpose capabilities
- Include comprehensive keywords
- Test before submitting
- Provide language-specific search terms
- Add service notes for infrastructure categories

**DON'T:**
- Add domain-specific categories (e.g., "machine learning model trainer")
- Duplicate existing categories
- Use overly specific keywords
- Skip testing
- Add categories with <3 packages per ecosystem

## Questions?

- Review existing categories in `src/priorart/data/taxonomy.yaml`
- Open an issue for discussion before adding complex categories
- Tag maintainers in your PR for review

## Maintenance

Categories are reviewed quarterly for:
- Keyword effectiveness (are matches accurate?)
- Search term quality (are top results relevant?)
- Service note accuracy (are managed alternatives still valid?)

If you notice a category performing poorly, please open an issue with examples.