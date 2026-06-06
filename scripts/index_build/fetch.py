"""
Fetch per-ecosystem package metadata for the priorart semantic index.

Two-source design:

- **Popularity lane** (top-N by ``dependent_packages_count`` or ``downloads``):
  reads slim per-ecosystem JSONL from the ``priorart/package-snapshot`` HF Hub
  dataset. The snapshot is produced by ``extract-snapshot.yml`` from
  ecosyste.ms's quarterly PostgreSQL dump, which avoids the deep counter-column
  ORDER BY queries that time out on the live API.
- **Recency lane** (top-N by ``latest_release_published_at``): hits the live
  ecosyste.ms API. The endpoint is indexed and reliable, and it captures
  packages too new to appear in the latest snapshot.

Each fetcher yields dicts with::

    {"name": str, "registry": str, "description": str, "github_url": str | None}

The driver (``build.py``) de-duplicates by ``(name, registry)``, assigns
integer keys, and writes a combined ``metadata.jsonl`` sidecar.

Set ``PRIORART_INDEX_FIXTURE`` to a directory of ``{ecosystem}.jsonl`` files
to skip the network and use local fixtures — useful for fast CI iteration.

Attribution: package metadata courtesy of ecosyste.ms, CC-BY-SA 4.0.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from collections.abc import Iterator
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


def _hf_download_with_retry(*, attempts: int = 5, base_delay: float = 5.0, **kwargs) -> str:
    """``hf_hub_download`` with exponential backoff on transient Hub errors.

    The Hub rate-limits bursty access (HTTP 429). huggingface_hub's built-in
    retry gives up within ~20s, which isn't enough when several shard builds
    hit the snapshot dataset at once — the 429 surfaces as a fatal
    ``LocalEntryNotFoundError``. Retry with exponential backoff + jitter so a
    short rate-limit window is ridden out instead of failing the build.
    """
    from huggingface_hub import hf_hub_download  # type: ignore

    for attempt in range(attempts):
        try:
            return hf_hub_download(**kwargs)
        except Exception as e:
            if attempt == attempts - 1:
                raise
            delay = base_delay * 2**attempt + random.uniform(0, base_delay)
            logger.warning(
                "HF download failed (attempt %d/%d): %s; retrying in %.0fs",
                attempt + 1,
                attempts,
                e,
                delay,
            )
            time.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


ECOSYSTE_MS_API = "https://packages.ecosyste.ms/api/v1"
SNAPSHOT_REPO_ID = "priorart/package-snapshot"

# Per-ecosystem registry slug + popularity sort key. The slug is only used for
# the recency lane (live API); the popularity lane reads the snapshot dataset.
# npm uses ``downloads`` because dependent-count under-ranks npm-specific
# tooling that ships as standalone CLIs rather than libraries others depend on.
ECOSYSTEM_CONFIG = {
    "python": {
        "slug": "pypi.org",
        "popularity_key": "dependent_packages_count",
        "registry": "pypi",
    },
    "npm": {"slug": "npmjs.org", "popularity_key": "downloads", "registry": "npm"},
    "crates": {
        "slug": "crates.io",
        "popularity_key": "dependent_packages_count",
        "registry": "cargo",
    },
    "go": {
        "slug": "proxy.golang.org",
        "popularity_key": "dependent_packages_count",
        "registry": "go",
    },
    "maven": {
        "slug": "repo1.maven.org",
        "popularity_key": "dependent_packages_count",
        "registry": "maven",
    },
    "nuget": {
        "slug": "nuget.org",
        "popularity_key": "dependent_packages_count",
        "registry": "nuget",
    },
}

PER_PAGE = 1000
REQUEST_TIMEOUT = 60
# Recency lane is on an indexed column and rarely fails, but transient 5xx
# during page turns still warrant a few retries.
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


def _iter_popular_snapshot(ecosystem: str, top_n: int) -> Iterator[dict]:
    """Yield up to ``top_n`` records from the HF Hub snapshot, ranked by entrenchment."""
    cfg = ECOSYSTEM_CONFIG[ecosystem]
    sort_key = cfg["popularity_key"]
    registry = cfg["registry"]

    path = _hf_download_with_retry(
        repo_id=SNAPSHOT_REPO_ID,
        filename=f"{ecosystem}.jsonl",
        repo_type="dataset",
        token=os.environ.get("HF_TOKEN") or None,
    )

    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    rows.sort(key=lambda r: r.get(sort_key) or 0, reverse=True)
    logger.info(
        "snapshot %s: %d rows; yielding top %d by %s",
        ecosystem,
        len(rows),
        top_n,
        sort_key,
    )

    for row in rows[:top_n]:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        # Empty descriptions are surprisingly common on Go and old PyPI rows.
        # Since ranking already selected the package, fall back to the name
        # rather than dropping a top-ranked entry.
        desc = (row.get("description") or "").strip() or name
        yield {
            "name": name,
            "registry": registry,
            "description": desc,
            "github_url": row.get("repository_url") or None,
        }


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


def _iter_recent_ms(ecosystem: str, top_n: int) -> Iterator[dict]:
    """Recency lane: top-N by ``latest_release_published_at`` via the live API.

    Captures rising-star packages (uv, ruff, bun during their first months)
    that haven't yet propagated into the quarterly snapshot. The endpoint
    sorts on an indexed column, so it reliably stays under the upstream
    proxy's timeout even on the largest registries.
    """
    cfg = ECOSYSTEM_CONFIG[ecosystem]
    slug = cfg["slug"]
    registry = cfg["registry"]
    url = f"{ECOSYSTE_MS_API}/registries/{slug}/packages"

    per_page = min(PER_PAGE, top_n)
    yielded = 0
    page = 1
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        while yielded < top_n:
            rows = _get_with_retry(
                client,
                url,
                {
                    "sort": "latest_release_published_at",
                    "order": "desc",
                    "per_page": per_page,
                    "page": page,
                },
            )
            if not rows:
                logger.warning(
                    "ecosyste.ms %s recency: empty/failed page %d, stopping at %d records",
                    ecosystem,
                    page,
                    yielded,
                )
                return
            for row in rows:
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                desc = (row.get("description") or "").strip() or name
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


def fetch_ecosystem(ecosystem: str, top_n: int = 20_000, recency_n: int = 2_000) -> Iterator[dict]:
    """Yield package records for ``ecosystem``.

    Prefers a local fixture if ``PRIORART_INDEX_FIXTURE`` is set; otherwise
    reads the popularity slice from the HF Hub snapshot and the recency slice
    from the live API. ``top_n`` caps the popularity slice, ``recency_n`` caps
    the recency slice (disabled when 0). Records are deduped by
    ``(name, registry)`` — popularity wins on ties.
    """
    fixture = list(_iter_fixture(ecosystem))
    if fixture:
        logger.info(f"Using fixture for {ecosystem} ({len(fixture)} records)")
        yield from fixture
        return

    seen: set[tuple[str, str]] = set()
    for rec in _iter_popular_snapshot(ecosystem, top_n):
        key = (rec["name"], rec["registry"])
        if key in seen:
            continue
        seen.add(key)
        yield rec

    if recency_n <= 0:
        return

    logger.info(f"Adding recency lane for {ecosystem} (top {recency_n} by recent release)")
    added = 0
    for rec in _iter_recent_ms(ecosystem, recency_n):
        key = (rec["name"], rec["registry"])
        if key in seen:
            continue
        seen.add(key)
        added += 1
        yield rec
    logger.info(f"Recency lane contributed {added} additional records for {ecosystem}")
