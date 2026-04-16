# Agent Configuration Guide

## Decision Protocol

### When to Call `find_alternatives`

**Call when:**
- Implementing general-purpose infrastructure (http clients, parsers, databases, queues, etc.)
- At the START of feature implementation, before writing code
- Discovering packages in an unfamiliar ecosystem

**Do NOT call when:**
- User explicitly names a specific library ("use requests", "install axios")
- Task is project-specific business logic
- Already using a well-established package for the task
- User says "just implement it" or "build it from scratch"

## Interpreting Results

### Score >= 75: `use_existing`

Use this package confidently. No further investigation needed.

```
"requests" — health_score: 82, recommendation: use_existing
→ Install and proceed.
```

### Score 50-74: `evaluate`

Call `ingest_repo` on the top 1-2 candidates to verify the interface fits your needs.

```
"httpx" — health_score: 72, recommendation: evaluate
→ Call ingest_repo, review interface, then decide.
```

### Score < 50: `build`

Consider building custom or using standard library. Warn user about maintenance risks.

```
"obscure-http" — health_score: 38, likely_abandoned: true
→ Implement lightweight wrapper using stdlib instead.
```

### Service Notes

Some categories return a `service_note` suggesting managed alternatives (e.g., Auth0 for authentication). Always present both options to the user.

## When to Call `ingest_repo`

**Use when:**
- Package scored 50-74 (evaluate) — verify interface
- Comparing top 2 similar candidates
- Registry description is unclear, need full README

**Don't use when:**
- Package scored >= 75 (already confident)
- Package scored < 50 (won't use anyway)
- You just need basic metadata (already in `find_alternatives` output)

Call on ONE candidate at a time — ingestion is expensive.

## Warning Flags

### `identity_verified: false`
Possible typosquatting. Treat with extreme caution. Recommend verified alternatives.

### `license_warning: true`
Copyleft license (GPL, AGPL). Inform user about open-source obligations before proceeding.

### `likely_abandoned: true`
No commits in 540+ days. Warn user and suggest alternatives.

### `dep_health_flag: true`
Dependencies with known vulnerabilities. Check if a newer version addresses the issues.

## Usage Patterns

### Pattern 1: Confident Use
```
User: "I need HTTP requests in Python"
1. find_alternatives(language="python", task_description="http client")
2. requests scores 82 → use_existing
3. Install and implement. No ingest_repo needed.
```

### Pattern 2: Evaluation
```
User: "I need a rate limiter"
1. find_alternatives(language="python", task_description="rate limiter")
2. slowapi scores 68 → evaluate
3. ingest_repo on slowapi → review interface → proceed
```

### Pattern 3: No Match
```
User: "Make my app handle data better"
1. find_alternatives returns no_taxonomy_match
2. Ask user to be specific: "Database ORM? Data validation? Caching?"
3. Re-call with clarified description
```

## Best Practices

1. **Call early** — before writing implementation code, not after
2. **Trust scores >= 75** — don't over-investigate strong packages
3. **Present service notes** — always surface managed alternatives when provided
4. **Be selective with ingestion** — only for evaluate-tier packages or direct comparisons
