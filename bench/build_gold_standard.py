"""
Build the benchmark gold standard from awesome-list README snapshots.

Sources (curated, heavily cross-linked awesome lists with stable category
headings and predominantly GitHub-linked entries):

- https://github.com/vinta/awesome-python
- https://github.com/sindresorhus/awesome-nodejs
- https://github.com/rust-unofficial/awesome-rust
- https://github.com/avelino/awesome-go

Heuristic:
- ``## Heading`` and ``### Heading`` mark categories. Skip meta sections
  (Contents, Contributing, License, Further Reading, Resources).
- Within a category, bullet lines (``* [name](https://github.com/owner/repo)``
  or ``- [name](...)``) are relevant packages. The linked GitHub repo name
  becomes the canonical identifier for scoring against registry names.
- A category must have ≥3 GitHub-linked entries to survive; smaller sets
  are noisy.

The script is offline-replayable: README markdown is pinned under
``bench/fixtures/awesome-snapshots/``. Regenerating is a deliberate act
(``python bench/build_gold_standard.py --refresh`` fetches fresh copies).

Refresh protocol (intentional and manual — there is no CI cron). A benchmark
answer key must stay a frozen, comparable artifact between runs; auto-churning
it breaks score comparability and ships drift without an evaluation signal. So
refresh only when the source materially changes (e.g. adding an ecosystem), and:

1. ``python bench/build_gold_standard.py --refresh`` to fetch fresh snapshots.
2. Review the ``gold_standard.jsonl`` diff for category drift — the awesome-list
   source is noisy (meta-list leaks, application-vs-library conflation).
3. Run ``python -m bench.run`` against the new fixture and record the score
   delta, so a fixture change is never merged without an evaluation signal.
4. Commit the reviewed fixture and its snapshots together.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path

import httpx

SNAPSHOT_DIR = Path(__file__).parent / "fixtures" / "awesome-snapshots"
OUTPUT_PATH = Path(__file__).parent / "fixtures" / "gold_standard.jsonl"
REWRITES_PATH = Path(__file__).parent / "fixtures" / "gold_standard_rewrites.jsonl"
APP_FILTER_CACHE = Path(__file__).parent / "fixtures" / ".app_filter_cache.json"

# Haiku 4.5 — small, cheap, ~$1/M input tokens + ~$5/M output tokens. Empirically:
# 305 categories * (~150 in + ~80 out) ≈ 46K in + 24K out ≈ $0.17. The default
# $1.0 budget cap gives ~6× safety margin against pricing drift / retries.
REWRITE_MODEL = "claude-haiku-4-5-20251001"
REWRITE_MAX_TOKENS = 200
REWRITE_INPUT_COST_PER_TOKEN = 1.0 / 1_000_000
REWRITE_OUTPUT_COST_PER_TOKEN = 5.0 / 1_000_000
REWRITE_PROMPT_TEMPLATE = (
    "Given a software-package category heading and language, generate exactly 3 "
    "natural developer search queries that an engineer would type into a package "
    "search tool. Be concrete and varied — include canonical phrasing AND "
    "niche/constrained variants. Heading: {heading} | Language: {language}. "
    "Return as JSON array of exactly 3 strings, no preamble, no markdown."
)

# Crates.io categories that mark an entry as an end-user binary tool, not a
# library. Hits any → drop. Awesome-rust mixes both heavily (e.g. its
# "Applications" heading is the original trigger for the application filter).
RUST_APP_CATEGORIES = frozenset(
    {
        "command-line-utilities",
        "command-line-interface",
        "development-tools::cargo-plugins",
    }
)

# PyPI Trove classifiers that, *in absence of any* "Topic :: Software
# Development :: Libraries*" classifier, mark a package as an end-user app.
# Presence of a Library classifier is the unambiguous keep signal — these
# only fire when the package omits it.
PYPI_APP_CLASSIFIERS = frozenset(
    {
        "Environment :: Console",
        "Topic :: Desktop Environment",
        "Topic :: Games/Entertainment",
        "Topic :: System :: Shells",
        "Topic :: Multimedia :: Sound/Audio :: Players",
    }
)
PYPI_LIBRARY_CLASSIFIER_PREFIX = "Topic :: Software Development :: Libraries"
PYPI_APP_NAME_SUFFIXES = ("-cli", "-tool", "-cmd")
HTTP_USER_AGENT = "priorart-bench/0.1"

SOURCES = [
    (
        "python",
        "awesome-python.md",
        "https://raw.githubusercontent.com/vinta/awesome-python/master/README.md",
    ),
    (
        "javascript",
        "awesome-nodejs.md",
        "https://raw.githubusercontent.com/sindresorhus/awesome-nodejs/main/readme.md",
    ),
    (
        "rust",
        "awesome-rust.md",
        "https://raw.githubusercontent.com/rust-unofficial/awesome-rust/main/README.md",
    ),
    ("go", "awesome-go.md", "https://raw.githubusercontent.com/avelino/awesome-go/main/README.md"),
]

SKIP_HEADINGS = {
    "contents",
    "contributing",
    "license",
    "further reading",
    "resources",
    "related lists",
    "services",
    "sites",
    "websites",
    "podcasts",
    "books",
    "newsletters",
    "weekly",
    "conferences",
    "tutorials",
    "videos",
    "blogs",
    "table of contents",
    "community",
    "awesome",
    "anti-features",
}

HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")
BULLET_RE = re.compile(r"^\s*[-*]\s+\[([^\]]+)\]\(https?://github\.com/([^/\s)]+)/([^/\s)#]+)")
MIN_ENTRIES = 3

# Repo slugs that match an awesome-list / meta-curation pattern are linked from
# parent awesome-lists but are not installable packages, so retrieval will never
# return them. Including them in the relevant set is pure noise that deflates
# nDCG/Recall.
META_REPO_RE = re.compile(r"^awesome[-_]", re.IGNORECASE)


def fetch_snapshots() -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for _, filename, url in SOURCES:
            resp = client.get(url)
            resp.raise_for_status()
            (SNAPSHOT_DIR / filename).write_text(resp.text, encoding="utf-8")
            print(f"fetched {filename} ({len(resp.text):,} chars)")


def _normalize_heading(text: str) -> str:
    # Strip anchors like "Category <a ...>" and trailing markdown noise.
    cleaned = re.sub(r"<[^>]+>", "", text)
    cleaned = re.sub(r"[\[\]]", "", cleaned).strip()
    return cleaned


def parse_sections(markdown: str) -> Iterable[tuple[str, list[str]]]:
    """Yield (heading, [repo_names]) for each category with ≥MIN_ENTRIES repos."""
    current_heading: str | None = None
    current_repos: list[str] = []

    for raw_line in markdown.splitlines():
        heading_match = HEADING_RE.match(raw_line)
        if heading_match:
            if current_heading and len(current_repos) >= MIN_ENTRIES:
                yield current_heading, current_repos
            current_heading = _normalize_heading(heading_match.group(2))
            current_repos = []
            continue

        bullet_match = BULLET_RE.match(raw_line)
        if bullet_match and current_heading:
            repo = bullet_match.group(3).rstrip(".,;:")
            if not repo or META_REPO_RE.match(repo):
                continue
            # Lowercase to match registry-canonical names (PyPI/npm/crates.io
            # are case-insensitive; pkg.go.dev varies but lowercases dominate).
            repo = repo.lower()
            if repo not in current_repos:
                current_repos.append(repo)

    if current_heading and len(current_repos) >= MIN_ENTRIES:
        yield current_heading, current_repos


def _load_app_filter_cache() -> dict[str, dict]:
    """Cache key is f"{language}:{name}". Value: registry payload or marker dict.

    The marker ``{"_status": "404"}`` records "registry didn't have it" so we
    don't retry every regen; ``{"_status": "error"}`` records a transient
    failure and is also persisted (fail-open: we keep the package).
    """
    if not APP_FILTER_CACHE.exists():
        return {}
    try:
        return json.loads(APP_FILTER_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_app_filter_cache(cache: dict[str, dict]) -> None:
    APP_FILTER_CACHE.parent.mkdir(parents=True, exist_ok=True)
    APP_FILTER_CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def _fetch_pypi(client: httpx.Client, name: str, cache: dict[str, dict]) -> dict | None:
    """Return a *trimmed* PyPI payload `{"info": {"classifiers": [...]}}`.

    PyPI's full ``/pypi/<name>/json`` ships the entire release history (often
    multi-MB per package). The filter only cares about ``info.classifiers``, so
    cache a slim projection — keeps the on-disk cache to a sane KB-scale.
    """
    key = f"python:{name}"
    if key in cache:
        cached = cache[key]
        if "_status" in cached:
            return None
        return cached
    try:
        resp = client.get(f"https://pypi.org/pypi/{name}/json", timeout=5.0)
    except httpx.HTTPError:
        cache[key] = {"_status": "error"}
        return None
    if resp.status_code == 404:
        cache[key] = {"_status": "404"}
        return None
    if resp.status_code != 200:
        cache[key] = {"_status": "error"}
        return None
    try:
        payload = resp.json()
    except ValueError:
        cache[key] = {"_status": "error"}
        return None
    classifiers = payload.get("info", {}).get("classifiers") or []
    trimmed = {"info": {"classifiers": list(classifiers)}}
    cache[key] = trimmed
    return trimmed


def _fetch_crates(client: httpx.Client, name: str, cache: dict[str, dict]) -> dict | None:
    """Return a trimmed crates.io payload — only ``categories`` is consulted."""
    key = f"rust:{name}"
    if key in cache:
        cached = cache[key]
        if "_status" in cached:
            return None
        return cached
    # crates.io requires a User-Agent or returns 403; we set it on the client.
    try:
        resp = client.get(f"https://crates.io/api/v1/crates/{name}", timeout=5.0)
    except httpx.HTTPError:
        cache[key] = {"_status": "error"}
        return None
    if resp.status_code == 404:
        cache[key] = {"_status": "404"}
        return None
    if resp.status_code != 200:
        cache[key] = {"_status": "error"}
        return None
    try:
        payload = resp.json()
    except ValueError:
        cache[key] = {"_status": "error"}
        return None
    categories = payload.get("categories") or []
    trimmed = {"categories": [{"slug": c.get("slug")} for c in categories if isinstance(c, dict)]}
    cache[key] = trimmed
    return trimmed


def _is_pypi_application(payload: dict, name: str) -> bool:
    """True when PyPI metadata strongly suggests an end-user app, not a library."""
    classifiers = payload.get("info", {}).get("classifiers") or []
    if any(c.startswith(PYPI_LIBRARY_CLASSIFIER_PREFIX) for c in classifiers):
        # Library classifier present → keep, regardless of other signals.
        return False
    # No Library classifier — look for explicit app classifiers.
    if any(c in PYPI_APP_CLASSIFIERS for c in classifiers):
        return True
    # Fallback: a -cli/-tool/-cmd suffix combined with no Library classifier
    # is a high-confidence app signal.
    if any(name.endswith(suf) for suf in PYPI_APP_NAME_SUFFIXES):
        return True
    return False


def _is_crates_application(payload: dict) -> bool:
    """True when any crates.io category slug marks the crate as an end-user binary."""
    categories = payload.get("categories") or []
    slugs = {c.get("slug") for c in categories if isinstance(c, dict)}
    return bool(slugs & RUST_APP_CATEGORIES)


def _filter_applications(repos: list[str], language: str) -> list[str]:
    """Drop end-user applications using PyPI Trove classifiers + crates.io categories.

    Fail-open everywhere: 404 / network error / parse failure means we trust
    awesome-list curation and keep the package. The whole filter is best-effort
    cleanup, not a correctness gate.

    npm and Go skip the filter (no canonical app-vs-library signal in those
    registries) — the application filter accepts the awesome-list noise there.
    """
    if language not in {"python", "rust"} or not repos:
        return list(repos)
    cache = _load_app_filter_cache()
    headers = {"User-Agent": HTTP_USER_AGENT}
    kept: list[str] = []
    with httpx.Client(headers=headers, follow_redirects=True) as client:
        for name in repos:
            try:
                if language == "python":
                    payload = _fetch_pypi(client, name, cache)
                    if payload is not None and _is_pypi_application(payload, name):
                        continue
                elif language == "rust":
                    payload = _fetch_crates(client, name, cache)
                    if payload is not None and _is_crates_application(payload):
                        continue
            except Exception:
                # Belt-and-braces: any unexpected error → keep the package.
                pass
            kept.append(name)
    _save_app_filter_cache(cache)
    return kept


def _grade(repos: list[str]) -> dict[str, int]:
    """Top-3 entries are maintainer's "top picks" (grade=2); rest are grade=1.

    Awesome-list curators tend to put the canonical / most-recommended packages
    first within each category — using position as a graded relevance signal
    is a free, calibration-free way to lift nDCG above pure binary.
    """
    return {repo: (2 if i < 3 else 1) for i, repo in enumerate(repos)}


def build_records(*, filter_apps: bool = False) -> list[dict]:
    # Awesome-lists repeat a category heading across sections (e.g. Rust lists
    # "Database" more than once), which would otherwise emit two rows for the
    # same (query, language) and double-count that query in the per-language
    # mean. Key by (query, language) and union the relevant sets — keeping the
    # higher grade on conflict — so each query appears exactly once.
    by_key: dict[tuple[str, str], dict[str, int]] = {}
    order: list[tuple[str, str]] = []
    for language, filename, _ in SOURCES:
        path = SNAPSHOT_DIR / filename
        if not path.exists():
            print(f"missing snapshot: {path} (run with --refresh)")
            continue
        markdown = path.read_text(encoding="utf-8")
        for heading, repos in parse_sections(markdown):
            key = heading.lower()
            if key in SKIP_HEADINGS or any(k in key for k in SKIP_HEADINGS):
                continue
            if filter_apps:
                repos = _filter_applications(repos, language)
            if len(repos) < MIN_ENTRIES:
                # Filter may drop the category below the floor; skip rather
                # than emit a one-or-two-entry row that's noise for nDCG.
                continue
            record_key = (key, language)
            grades = _grade(repos)
            if record_key in by_key:
                existing = by_key[record_key]
                for repo, grade in grades.items():
                    existing[repo] = max(existing.get(repo, 0), grade)
            else:
                by_key[record_key] = grades
                order.append(record_key)
    return [
        {"query": query, "language": language, "relevant_grades": by_key[(query, language)]}
        for query, language in order
    ]


def write_records(records: list[dict]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    print(f"wrote {len(records)} records → {OUTPUT_PATH}")


def _parse_rewrite_response(text: str) -> list[str] | None:
    """Extract a 3-string JSON array from the Haiku response. None on any malformedness.

    Haiku is generally well-behaved with the "no preamble, no markdown" instruction
    but we tolerate the occasional ```json``` fence by stripping it before parsing.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Trim a leading ```json / ``` fence and the trailing ```.
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, list) or len(parsed) != 3:
        return None
    if not all(isinstance(item, str) and item.strip() for item in parsed):
        return None
    return [item.strip() for item in parsed]


def rewrite_queries_via_llm(
    input_path: Path,
    output_path: Path,
    budget_usd: float = 1.0,
) -> int:
    """Expand each gold-standard heading into 3 realistic developer queries via Haiku.

    Lazy-imports `anthropic` so the bench harness doesn't hard-require the SDK.
    Install on demand: ``pip install anthropic`` (intentionally NOT a project dep).

    Each rewrite inherits the parent row's ``relevant_grades`` and ``language`` —
    the queries differ but the relevance judgments are shared, expanding retrieval
    coverage without re-curation.

    Returns count of output rows written; 0 on graceful skip (missing SDK / key).
    """
    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError:
        print(
            "anthropic SDK not installed; install with `pip install anthropic`. Skipping rewrites.",
            file=sys.stderr,
        )
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "ANTHROPIC_API_KEY not set; export it to run rewrites. Skipping.",
            file=sys.stderr,
        )
        return 0

    if not input_path.exists():
        raise SystemExit(f"input not found: {input_path}")

    rows: list[dict] = []
    with input_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))

    client = anthropic.Anthropic(api_key=api_key)

    written = 0
    rewritten_categories = 0
    estimated_cost = 0.0
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as out:
        for row in rows:
            # Cost-cap before each call so we never overshoot — Haiku usage
            # objects expose token totals only post-call, hence the pre-check.
            if estimated_cost >= budget_usd:
                print(
                    f"Budget cap ${budget_usd:.2f} reached after "
                    f"{rewritten_categories} categories; stopping.",
                    file=sys.stderr,
                )
                break

            heading = row.get("query", "")
            language = row.get("language", "")
            grades = row.get("relevant_grades", {})
            prompt = REWRITE_PROMPT_TEMPLATE.format(heading=heading, language=language)

            try:
                response = client.messages.create(
                    model=REWRITE_MODEL,
                    max_tokens=REWRITE_MAX_TOKENS,
                    messages=[{"role": "user", "content": prompt}],
                )
            except Exception as exc:  # noqa: BLE001 — network/auth failures shouldn't kill the batch
                print(f"warn: API call failed for '{heading}': {exc}", file=sys.stderr)
                continue

            usage = getattr(response, "usage", None)
            if usage is not None:
                # Haiku 4.5 tokens are cheap but not free; track running cost.
                input_tokens = getattr(usage, "input_tokens", 0) or 0
                output_tokens = getattr(usage, "output_tokens", 0) or 0
                estimated_cost += (
                    input_tokens * REWRITE_INPUT_COST_PER_TOKEN
                    + output_tokens * REWRITE_OUTPUT_COST_PER_TOKEN
                )

            try:
                text = response.content[0].text
            except (AttributeError, IndexError, TypeError):
                print(f"warn: malformed response for '{heading}'", file=sys.stderr)
                continue

            queries = _parse_rewrite_response(text)
            if queries is None:
                print(
                    f"warn: could not parse 3-string JSON array for '{heading}'",
                    file=sys.stderr,
                )
                continue

            for query in queries:
                out.write(
                    json.dumps(
                        {
                            "query": query,
                            "language": language,
                            "relevant_grades": grades,
                        }
                    )
                    + "\n"
                )
                written += 1
            rewritten_categories += 1

    print(
        f"Rewrote {rewritten_categories} categories → {written} output rows. "
        f"Estimated cost: ${estimated_cost:.2f}."
    )
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--refresh",
        action="store_true",
        help="Fetch fresh snapshots from upstream before parsing",
    )
    mode.add_argument(
        "--rewrite",
        action="store_true",
        help=(
            "Expand each gold-standard heading into 3 realistic developer queries via "
            "Claude Haiku (requires ANTHROPIC_API_KEY and `pip install anthropic`). "
            "Mutually exclusive with --refresh."
        ),
    )
    parser.add_argument(
        "--filter-applications",
        action="store_true",
        help=(
            "Drop end-user applications (CLI tools, games, desktop apps) by checking "
            "PyPI Trove classifiers and crates.io categories. Off by default — opting "
            "in makes network calls (cached under bench/fixtures/.app_filter_cache.json)."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=OUTPUT_PATH,
        help="Input JSONL (default: bench/fixtures/gold_standard.jsonl). --rewrite only.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REWRITES_PATH,
        help="Output JSONL (default: bench/fixtures/gold_standard_rewrites.jsonl). --rewrite only.",
    )
    parser.add_argument(
        "--budget-usd",
        type=float,
        default=1.0,
        help="Cost ceiling for the rewrite run (default: $1.00). --rewrite only.",
    )
    args = parser.parse_args()

    if args.rewrite:
        rewrite_queries_via_llm(args.input, args.output, budget_usd=args.budget_usd)
        return

    if args.refresh:
        fetch_snapshots()

    records = build_records(filter_apps=args.filter_applications)
    if not records:
        raise SystemExit(
            "No records produced — check snapshots under bench/fixtures/awesome-snapshots/"
        )
    write_records(records)


if __name__ == "__main__":
    main()
