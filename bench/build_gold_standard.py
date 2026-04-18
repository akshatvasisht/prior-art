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
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable
from pathlib import Path

import httpx

SNAPSHOT_DIR = Path(__file__).parent / "fixtures" / "awesome-snapshots"
OUTPUT_PATH = Path(__file__).parent / "fixtures" / "gold_standard.jsonl"

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
            repo = bullet_match.group(3)
            repo = repo.rstrip(".,;:")
            if repo and repo not in current_repos:
                current_repos.append(repo)

    if current_heading and len(current_repos) >= MIN_ENTRIES:
        yield current_heading, current_repos


def build_records() -> list[dict]:
    records: list[dict] = []
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
            records.append(
                {
                    "query": heading.lower(),
                    "language": language,
                    "relevant": repos,
                }
            )
    return records


def write_records(records: list[dict]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    print(f"wrote {len(records)} records → {OUTPUT_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Fetch fresh snapshots from upstream before parsing",
    )
    args = parser.parse_args()

    if args.refresh:
        fetch_snapshots()

    records = build_records()
    if not records:
        raise SystemExit(
            "No records produced — check snapshots under bench/fixtures/awesome-snapshots/"
        )
    write_records(records)


if __name__ == "__main__":
    main()
