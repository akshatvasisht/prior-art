# Coding Standards & Style Guide

## General Principles

### Professionalism & Tone
* All comments and documentation must use objective, technical language.
* Avoid informal language or environment-specific justifications.
* **Correct:** "Defaults to CPU inference for wider hardware compatibility."
* **Incorrect:** "Running on my laptop so GPU wasn't available."

### Intent over Implementation
* Comments must explain *why* a decision was made, not narrate *what* the code does.
* The code itself should be self-explanatory for the *what*.

### No Meta-Commentary
* Forbid internal debate traces, failed attempt logs, or editing notes in committed code.
* **Correct:** `// Uses Y to ensure thread safety under concurrent load`
* **Incorrect:** `// I tried using X but it kept breaking so I switched to Y`

## Language Guidelines

### [Language Name]
* **Naming:** [e.g., camelCase for vars, PascalCase for classes]
* **Type Safety:** [e.g., Strict typing required]
* **Async/Await:** [e.g., Prefer async/await over promises]

## Git Workflow
* **Branches:** Name branches systematically: `feature/description`, `fix/description`, `chore/description`.
* **Commits:** Messages must use imperative mood, present tense, and be under 72 characters for the subject line. Commits must be atomic (one logical change per commit).
* **Pull Requests:** PRs must include a description of what changed and why.

## Code Comments
* Trivial logic must not be commented.
* Complex logic, non-obvious decisions, and "gotchas" must be commented.
* Commented-out code must not be committed unless accompanied by a TODO with a ticket/issue reference.
