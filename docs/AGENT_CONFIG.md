# Agent Configuration Guide for `priorart`

This guide explains how AI agents should use the `priorart` tools to make optimal build-vs-borrow decisions.

## Decision Protocol

### When to Call `find_alternatives`

**Call this tool when:**
- Implementing a new feature that requires general-purpose infrastructure (http clients, parsers, databases, queues, etc.)
- At the START of feature implementation, before writing any code
- Discovering packages in an unfamiliar ecosystem
- User asks "what's the best library for X?"

**DO NOT call this tool when:**
- User explicitly names a specific library ("use requests", "install axios")
- Task is project-specific business logic (not reusable infrastructure)
- Already using a well-established package for the task (e.g., already using requests for HTTP)
- User says "just implement it" or "build it from scratch"

### Interpreting Results

Based on the `health_score` and `recommendation`:

#### Score ≥75: `use_existing`

**Action**: Use this package confidently without further investigation.

```
Agent sees:
{
  "name": "requests",
  "health_score": 82,
  "recommendation": "use_existing",
  "license": "Apache-2.0",
  "license_warning": false
}

Agent response:
"I'll use the `requests` library (health score: 82/100). It's a well-maintained
package with strong adoption and reliability. Installing..."
```

#### Score 50-74: `evaluate`

**Action**: Call `ingest_repo` on the top 1-2 candidates to deeply understand interfaces before deciding.

```
Agent sees:
{
  "name": "httpx",
  "health_score": 72,
  "recommendation": "evaluate",
  "url": "https://github.com/encode/httpx"
}

Agent response:
"httpx looks promising (score: 72/100) but I want to verify its interface
matches our needs. Let me examine the package..."

[Calls ingest_repo on httpx]
[Reviews extracted interface]

"After reviewing httpx's interface, it provides async HTTP support which
fits our requirements perfectly. I'll use this package."
```

#### Score <50: `build`

**Action**: Consider building a custom solution or look for managed service alternatives.

```
Agent sees:
{
  "name": "obscure-http",
  "health_score": 38,
  "recommendation": "build",
  "likely_abandoned": true,
  "weekly_downloads": 42
}

Agent response:
"The available packages have low health scores (<50), indicating maintenance risks.
I'll implement a lightweight HTTP wrapper using the standard library's urllib instead."
```

### Service Notes

Some categories return a `service_note` suggesting managed alternatives:

```json
{
  "status": "success",
  "packages": [...],
  "service_note": "Authentication is commonly solved by managed identity services
                   (Auth0, Okta, AWS Cognito). Consider evaluating managed
                   alternatives alongside these open source results."
}
```

**Action**: Present both options to the user:

```
Agent response:
"I found several open source authentication libraries, but this is a category
commonly served by managed services like Auth0 or Okta.

Would you prefer:
1. A managed authentication service (recommended for production)
2. An open source library like passport (score: 76/100)

Let me know your preference and I'll proceed accordingly."
```

## Tool Usage Patterns

### Pattern 1: Confident Use

```
User: "I need to make HTTP requests in my Python app"

1. Call find_alternatives(language="python", task_description="http client")
2. See requests (score: 82, use_existing)
3. Proceed with installation and implementation
4. No need to call ingest_repo
```

### Pattern 2: Evaluation Required

```
User: "I need a rate limiter for my API"

1. Call find_alternatives(language="python", task_description="rate limiter")
2. See slowapi (score: 68, evaluate) and limits (score: 65, evaluate)
3. Call ingest_repo on slowapi (top candidate)
4. Review interface - looks good
5. Proceed with slowapi
```

### Pattern 3: Build Custom

```
User: "I need an obscure XML parser with custom validation"

1. Call find_alternatives(language="python", task_description="xml parser")
2. See packages but none fit the "custom validation" requirement
3. Inform user: "Standard XML parsers exist (lxml score: 81), but your
   custom validation requirements suggest building a wrapper around lxml
   rather than using a different package."
```

### Pattern 4: No Taxonomy Match

```
User: "Make my app handle data better"

1. Call find_alternatives(language="python", task_description="handle data better")
2. Receive: {status: "no_taxonomy_match", hint: "Pass a concrete noun..."}
3. Ask clarification: "Could you be more specific? For example:
   - Database ORM?
   - Data validation?
   - Data transformation/pipeline?
   - Caching?"
4. User clarifies: "Data validation"
5. Call find_alternatives(language="python", task_description="data validation")
6. Proceed with results (pydantic score: 84)
```

## When to Call `ingest_repo`

### Call on ONE candidate at a time

```python
# CORRECT: Focused investigation
top_candidate = results['packages'][0]
if top_candidate['recommendation'] == 'evaluate':
    ingest_repo(top_candidate['url'], language="python")

# INCORRECT: Don't call on all packages
for pkg in results['packages']:  # Too expensive
    ingest_repo(pkg['url'])
```

### Use cases for `ingest_repo`:

1. **Evaluate recommendation**: Package scored 50-74, need to verify interface
2. **API surface verification**: Need to see exact function signatures
3. **Comparing 2 similar packages**: Examining differences between top 2 candidates
4. **Documentation quality**: Registry description unclear, need full README

### Don't use `ingest_repo` when:

- Package has score ≥75 (already confident)
- Package has score <50 (won't use anyway)
- You just need basic metadata (use registry data from `find_alternatives`)

## Warning Flags

### `identity_verified: false`

```json
{
  "name": "requestz",
  "identity_verified": false
}
```

**Action**: Treat with extreme caution - possible typosquatting or package name hijacking.

```
Agent response:
"Warning: The package 'requestz' could not be verified as legitimately
belonging to its claimed repository. This is a security red flag.
I recommend using 'requests' instead (verified, score: 82)."
```

### `license_warning: true`

```json
{
  "license": "GPL-3.0",
  "license_warning": true
}
```

**Action**: Inform user about copyleft implications.

```
Agent response:
"Note: This package uses GPL-3.0 (copyleft license), which may require
your application to be open-sourced. Confirm this is acceptable before proceeding."
```

### `likely_abandoned: true`

```json
{
  "likely_abandoned": true,
  "days_since_last_commit": 890
}
```

**Action**: Warn user and suggest alternatives.

```
Agent response:
"This package appears abandoned (no commits in 890 days). I recommend
using [alternative] instead, which is actively maintained."
```

### `dep_health_flag: true`

```json
{
  "dep_health_flag": true,
  "vulnerable_dep_count": 2
}
```

**Action**: Note vulnerability and check if fixable.

```
Agent response:
"This package has 2 dependencies with known vulnerabilities.
Check if a newer version addresses these issues before proceeding."
```

## Error Handling

### No Taxonomy Match

```json
{
  "status": "no_taxonomy_match",
  "message": "Could not confidently map task description...",
  "hint": "Pass a concrete noun describing the capability..."
}
```

**Action**: Ask user for clarification with specific examples.

### No Results

```json
{
  "status": "no_results",
  "message": "No packages found matching 'very obscure query'"
}
```

**Action**: Try broader query or inform user no existing solutions found.

### Below Threshold

```json
{
  "status": "below_threshold",
  "message": "All candidates were below minimum download/star thresholds"
}
```

**Action**: Inform user packages exist but quality is questionable - suggest building custom.

## Best Practices

### 1. Call Early

Call `find_alternatives` BEFORE writing implementation code, not after.

```python
# CORRECT sequence:
# 1. find_alternatives → discovered package X
# 2. Install package X
# 3. Implement feature using X

# INCORRECT sequence:
# 1. Implement custom solution
# 2. User asks "why didn't you use a library?"
# 3. find_alternatives → oops, package X exists
```

### 2. Trust the Scores

If a package scores ≥75, trust it and use it. Don't second-guess or over-investigate.

### 3. Respect Tool Descriptions

The tool descriptions contain explicit "DO NOT call when..." constraints. Follow them.

### 4. Present Service Notes

When a `service_note` is present, always present it to the user as an option.

### 5. Be Selective with Ingestion

Only call `ingest_repo` when truly needed (evaluate recommendation or comparing finalists).

## Example Complete Workflow

```
User: "Add authentication to my FastAPI app"

Agent:
1. Recognize "authentication" is general infrastructure
2. Call find_alternatives(language="python", task_description="authentication")
3. Receive results + service_note about Auth0/Okta
4. Present both options to user:

   "I can set up authentication using:

   Option A (Recommended): Managed service
   - Auth0, Okta, or AWS Cognito
   - Pros: Production-ready, scalable, maintained
   - Cons: External dependency, potential cost

   Option B: Open source library
   - python-jose (score: 76/100) - JWT handling
   - Pros: Self-hosted, no external dependencies
   - Cons: You maintain it, need to implement full flow

   Which would you prefer?"

5. User chooses Option B
6. Proceed with python-jose (score 76 = use_existing)
7. Install and implement without calling ingest_repo
```

## Summary

- **Call `find_alternatives` early** when implementing general-purpose capabilities
- **Trust scores ≥75** and use packages confidently
- **Investigate scores 50-74** with `ingest_repo` before deciding
- **Avoid scores <50** and consider custom solutions
- **Present service notes** when available
- **Respect warning flags** and inform users
- **Call `ingest_repo` sparingly** - only when truly needed
- **Follow tool constraints** - don't call when user names specific package