"""
Fetch per-ecosystem package metadata for the priorart semantic index.

Data source: ecosyste.ms Packages API (https://packages.ecosyste.ms). Free,
unauthenticated, CC-BY-SA 4.0 data. Returns name + description + repository_url +
dependent_packages_count + downloads for every package across PyPI, npm,
crates.io, and Go modules — exactly the fields we need for a
rank-by-entrenchment top-N slice.

Each fetcher yields dicts with:

    {"name": str, "registry": str, "description": str, "github_url": str | None}

The driver (`build.py`) de-duplicates by (name, registry), assigns integer keys,
and writes a combined ``metadata.jsonl`` sidecar.

Set ``PRIORART_INDEX_FIXTURE`` to a directory of ``{ecosystem}.jsonl`` files to
skip the network and use local fixtures — useful for fast CI iteration.

Attribution: package metadata courtesy of ecosyste.ms, CC-BY-SA 4.0.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

ECOSYSTE_MS_API = "https://packages.ecosyste.ms/api/v1"

# Per-ecosystem registry slug + ranking sort key. npm's
# sort=dependent_packages_count is a broken endpoint on the ecosyste.ms backend
# (returns 500); downloads is a valid popularity proxy and is what PyPI/npm
# registry search already uses in the runtime discovery path.
ECOSYSTEM_CONFIG = {
    "python": {"slug": "pypi.org", "sort": "dependent_packages_count", "registry": "pypi"},
    "npm": {"slug": "npmjs.org", "sort": "downloads", "registry": "npm"},
    "crates": {"slug": "crates.io", "sort": "dependent_packages_count", "registry": "cargo"},
    "go": {"slug": "proxy.golang.org", "sort": "dependent_packages_count", "registry": "go"},
    "maven": {"slug": "repo1.maven.org", "sort": "dependent_packages_count", "registry": "maven"},
    "nuget": {"slug": "nuget.org", "sort": "dependent_packages_count", "registry": "nuget"},
}

PER_PAGE = 1000
REQUEST_TIMEOUT = 60
# ecosyste.ms maven/nuget backends return intermittent 5xx on deep pages;
# extended backoff is required to keep primary-lane shards from truncating.
MAX_RETRIES = 5
RETRY_BACKOFF_SECONDS = 10


def _fixture_path(ecosystem: str) -> Path | None:
    base = os.environ.get("PRIORART_INDEX_FIXTURE")
    if not base:
        return None
    p = Path(base) / f"{ecosystem}.jsonl"
    return p if p.exists() else None


def _iter_fixture(ecosystem: str) -> Iterator[dict]:
    path = _fixture_path(ecosystem)
    if not path:
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _get_with_retry(client: httpx.Client, url: str, params: dict) -> list[dict] | None:
    """GET with retry/backoff. Returns parsed JSON list on success, None on exhaustion."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else None
            if 500 <= resp.status_code < 600:
                logger.warning(
                    "ecosyste.ms %s returned %d on attempt %d/%d",
                    url,
                    resp.status_code,
                    attempt,
                    MAX_RETRIES,
                )
            else:
                logger.error(
                    "ecosyste.ms %s returned %d (non-retriable): %s",
                    url,
                    resp.status_code,
                    resp.text[:200],
                )
                return None
        except httpx.RequestError as e:
            logger.warning(
                "ecosyste.ms %s network error on attempt %d/%d: %s",
                url,
                attempt,
                MAX_RETRIES,
                e,
            )
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    return None


def _iter_ecosystems_ms(ecosystem: str, top_n: int, sort_key: str | None = None) -> Iterator[dict]:
    """Page through ecosyste.ms and yield up-to-``top_n`` package records."""
    cfg = ECOSYSTEM_CONFIG[ecosystem]
    slug = cfg["slug"]
    sort = sort_key or cfg["sort"]
    registry = cfg["registry"]
    url = f"{ECOSYSTE_MS_API}/registries/{slug}/packages"

    # Fixed per_page across all pages: ecosyste.ms is offset-based, so shrinking
    # per_page between pages would shift the window and skip rows.
    per_page = min(PER_PAGE, top_n)
    yielded = 0
    page = 1
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        while yielded < top_n:
            rows = _get_with_retry(
                client,
                url,
                {"sort": sort, "order": "desc", "per_page": per_page, "page": page},
            )
            if not rows:
                logger.warning(
                    "ecosyste.ms %s: empty/failed page %d, stopping at %d records",
                    ecosystem,
                    page,
                    yielded,
                )
                return
            for row in rows:
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                desc = (row.get("description") or "").strip()
                if not desc:
                    # Go and some older PyPI entries frequently have empty
                    # descriptions on ecosyste.ms. Since ranking already selected
                    # them for popularity, keep the entry with name-as-description
                    # rather than dropping a top-ranked package.
                    desc = name
                yield {
                    "name": name,
                    "registry": registry,
                    "description": desc,
                    "github_url": row.get("repository_url") or None,
                }
                yielded += 1
                if yielded >= top_n:
                    return
            if len(rows) < per_page:
                return
            page += 1


def _iter_deps_dev(ecosystem: str, top_n: int) -> Iterator[dict]:
    """Primary ranking slice: top-N by dependent-package count (or downloads for npm)."""
    cfg = ECOSYSTEM_CONFIG[ecosystem]
    logger.info(
        "ecosyste.ms %s: fetching top %d by %s",
        ecosystem,
        top_n,
        cfg["sort"],
    )
    yield from _iter_ecosystems_ms(ecosystem, top_n)


def _iter_deps_dev_by_recency(ecosystem: str, top_n: int, window_days: int = 90) -> Iterator[dict]:
    """Recency slice: top-N by most recent release.

    Captures rising-star packages (uv, ruff, bun during their first months)
    that haven't accumulated reverse deps yet but are actively shipping. The
    ``window_days`` argument is preserved for API compatibility but unused —
    ecosyste.ms sort returns globally-latest releases, which naturally floats
    recent packages to the top.
    """
    logger.info(
        "ecosyste.ms %s: fetching top %d by latest_release_published_at",
        ecosystem,
        top_n,
    )
    yield from _iter_ecosystems_ms(ecosystem, top_n, sort_key="latest_release_published_at")


def fetch_ecosystem(ecosystem: str, top_n: int = 20_000, recency_n: int = 2_000) -> Iterator[dict]:
    """Yield package records for ``ecosystem``.

    Prefers a local fixture if ``PRIORART_INDEX_FIXTURE`` is set; otherwise hits
    the ecosyste.ms API. ``top_n`` caps the popularity-ranked primary slice;
    ``recency_n`` caps the recency-ranked secondary slice (disabled when 0).
    Records are deduped by (name, registry) — the primary slice wins on ties.
    """
    fixture = list(_iter_fixture(ecosystem))
    if fixture:
        logger.info(f"Using fixture for {ecosystem} ({len(fixture)} records)")
        yield from fixture
        return

    seen: set[tuple[str, str]] = set()
    for rec in _iter_deps_dev(ecosystem, top_n):
        key = (rec["name"], rec["registry"])
        if key in seen:
            continue
        seen.add(key)
        yield rec

    if recency_n <= 0:
        return

    logger.info(f"Adding recency lane for {ecosystem} (top {recency_n} by recent release)")
    added = 0
    for rec in _iter_deps_dev_by_recency(ecosystem, recency_n):
        key = (rec["name"], rec["registry"])
        if key in seen:
            continue
        seen.add(key)
        added += 1
        yield rec
    logger.info(f"Recency lane contributed {added} additional records for {ecosystem}")
