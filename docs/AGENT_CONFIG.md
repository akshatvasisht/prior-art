# Agent Configuration

Guidance for AI agents invoking the MCP tools exposed by `priorart`.

## `find_alternatives`

Invoke when:

- Implementing general-purpose infrastructure (HTTP clients, parsers, data stores, queues, serializers).
- Beginning a feature in an unfamiliar ecosystem.

Do not invoke when:

- The user has named a specific library.
- The task is project-specific business logic.
- An established package is already in use for the same purpose.

## Interpreting `recommendation`

| Value | Score | Action |
|---|---|---|
| `use_existing` | ≥ 75 | Adopt without further evaluation. |
| `evaluate` | 50–74 | Call `ingest_repo` on one or two top candidates to verify the interface. |
| `build` | < 50 | Consider a stdlib solution or a minimal in-house implementation. Surface `likely_abandoned` and other warning flags to the user. |

## `ingest_repo`

Invoke when:

- A candidate scored in the `evaluate` range and its public interface must be reviewed.
- Two candidates of similar score must be compared.
- The registry description is insufficient.

Do not invoke when:

- A package already scored `use_existing` or `build`.
- Only basic metadata is required (already present in `find_alternatives` output).

Ingestion clones the repository; invoke on one candidate at a time.

## Warning flags

| Field | Meaning | Recommended response |
|---|---|---|
| `identity_verified: false` | Package name and repository URL do not cross-reference; possible typosquatting. | Prefer a verified alternative; surface the warning to the user. |
| `license_warning: true` | Copyleft license (GPL, AGPL, etc.). | Disclose obligations before adoption. |
| `likely_abandoned: true` | No commits in the past 540 days. | Surface the warning and propose alternatives. |
| `dep_health_flag: true` | One or more dependencies have known vulnerabilities. | Check whether a newer release resolves them. |

## Usage patterns

```
Confident adoption
  find_alternatives(language="python", task_description="http client")
  → "requests" scores 82, recommendation=use_existing
  → adopt

Evaluation
  find_alternatives(language="python", task_description="rate limiter")
  → "slowapi" scores 68, recommendation=evaluate
  → ingest_repo(repo_url=...) → review interface → decide

Low-signal query
  find_alternatives returns low top-similarity
  → request a more specific task description from the user
  → re-invoke
```
