"""
Fetch per-ecosystem package metadata for the priorart semantic index.

Strategy:
- Primary source: deps.dev BigQuery public dataset. It unifies PyPI, npm, crates,
  and Go modules in a single schema (`bigquery-public-data.deps_dev_v1.*`).
- Per-ecosystem supplements for signals deps.dev doesn't expose well
  (e.g. PyPI description/summary fallback via the simple JSON API).

Each fetcher yields dicts with:

    {"name": str, "registry": str, "description": str, "github_url": str | None}

The driver (`build.py`) de-duplicates by (name, registry), assigns integer keys,
and writes a combined ``metadata.jsonl`` sidecar.

The deps.dev BigQuery queries can be swapped for locally downloaded CSVs or a
mock fixture in CI by setting ``PRIORART_INDEX_FIXTURE`` to a directory of
``{ecosystem}.jsonl`` files — useful for fast iteration without BigQuery auth.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

ECOSYSTEM_TO_DEPS_DEV_SYSTEM = {
    "python": "PYPI",
    "npm": "NPM",
    "crates": "CARGO",
    "go": "GO",
}


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


def _iter_deps_dev(ecosystem: str, top_n: int) -> Iterator[dict]:
    """Query deps.dev BigQuery for top-N packages by reverse-dep count.

    Deferred import — most local CI runs use the fixture path, so we don't
    want google-cloud-bigquery as a hard build dependency.
    """
    from google.cloud import bigquery  # type: ignore

    client = bigquery.Client()
    system = ECOSYSTEM_TO_DEPS_DEV_SYSTEM[ecosystem]

    query = f"""
    WITH latest AS (
      SELECT
        Name AS name,
        System AS system,
        ARRAY_AGG(Version ORDER BY UpstreamPublishedAt DESC LIMIT 1)[OFFSET(0)] AS latest_version
      FROM `bigquery-public-data.deps_dev_v1.PackageVersions`
      WHERE System = '{system}'
      GROUP BY Name, System
    ),
    rdeps AS (
      SELECT
        System AS system,
        Name AS name,
        Version AS version,
        COUNT(DISTINCT CONCAT(Dependent.System, ':', Dependent.Name)) AS rdep_count
      FROM `bigquery-public-data.deps_dev_v1.DependentsLatest`
      WHERE System = '{system}' AND MinimumDepth = 1
      GROUP BY System, Name, Version
    ),
    ranked AS (
      SELECT
        pv.Name AS name,
        pv.System AS system,
        pv.Description AS description,
        (
          SELECT URL FROM UNNEST(pv.Links) WHERE Label = 'SOURCE_REPO' LIMIT 1
        ) AS github_url,
        r.rdep_count AS rdeps
      FROM `bigquery-public-data.deps_dev_v1.PackageVersions` pv
      JOIN latest l
        ON pv.Name = l.name AND pv.System = l.system AND pv.Version = l.latest_version
      LEFT JOIN rdeps r
        ON pv.Name = r.name AND pv.System = r.system AND pv.Version = r.version
    )
    SELECT name, system, description, github_url
    FROM ranked
    ORDER BY COALESCE(rdeps, 0) DESC
    LIMIT {int(top_n)}
    """

    registry = _system_to_registry(system)
    for row in client.query(query).result():
        desc = (row["description"] or "").strip()
        if not desc:
            continue
        yield {
            "name": row["name"],
            "registry": registry,
            "description": desc,
            "github_url": row["github_url"],
        }


def _system_to_registry(system: str) -> str:
    return {
        "PYPI": "pypi",
        "NPM": "npm",
        "CARGO": "cargo",
        "GO": "go",
    }[system]


def _iter_deps_dev_by_recency(ecosystem: str, top_n: int, window_days: int = 90) -> Iterator[dict]:
    """Query deps.dev BigQuery for top-N packages with recent releases.

    A second slice layered on top of the reverse-dependency ranking. Captures
    rising-star packages (uv, ruff, bun during their first months) that haven't
    accumulated reverse deps yet but are actively shipping. Falls back to an
    empty iterator if BigQuery or the referenced tables are unavailable.
    """
    try:
        from google.cloud import bigquery  # type: ignore
    except ImportError:
        logger.info("google-cloud-bigquery not installed; skipping recency lane")
        return

    system = ECOSYSTEM_TO_DEPS_DEV_SYSTEM[ecosystem]
    query = f"""
    WITH recent AS (
      SELECT
        Name AS name,
        System AS system,
        MAX(UpstreamPublishedAt) AS last_published
      FROM `bigquery-public-data.deps_dev_v1.PackageVersions`
      WHERE System = '{system}'
        AND UpstreamPublishedAt >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {int(window_days)} DAY)
      GROUP BY Name, System
    ),
    ranked AS (
      SELECT
        pv.Name AS name,
        pv.System AS system,
        pv.Description AS description,
        (
          SELECT URL FROM UNNEST(pv.Links) WHERE Label = 'SOURCE_REPO' LIMIT 1
        ) AS github_url,
        r.last_published AS last_published
      FROM `bigquery-public-data.deps_dev_v1.PackageVersions` pv
      JOIN recent r
        ON pv.Name = r.name AND pv.System = r.system
      WHERE pv.UpstreamPublishedAt = r.last_published
    )
    SELECT name, system, description, github_url
    FROM ranked
    ORDER BY last_published DESC
    LIMIT {int(top_n)}
    """

    registry = _system_to_registry(system)
    try:
        client = bigquery.Client()
        rows = list(client.query(query).result())
    except Exception as e:
        logger.info(f"Recency lane skipped for {ecosystem}: {e}")
        return

    for row in rows:
        desc = (row["description"] or "").strip()
        if not desc:
            continue
        yield {
            "name": row["name"],
            "registry": registry,
            "description": desc,
            "github_url": row["github_url"],
        }


def fetch_ecosystem(ecosystem: str, top_n: int = 20_000, recency_n: int = 2_000) -> Iterator[dict]:
    """Yield package records for ``ecosystem``.

    Prefers a local fixture if ``PRIORART_INDEX_FIXTURE`` is set; otherwise hits
    deps.dev BigQuery. ``top_n`` caps the reverse-dep-ranked primary slice;
    ``recency_n`` caps the stars-velocity secondary slice (disabled when 0).
    Records are deduped by (name, registry) — the primary slice wins on ties.
    """
    fixture = list(_iter_fixture(ecosystem))
    if fixture:
        logger.info(f"Using fixture for {ecosystem} ({len(fixture)} records)")
        yield from fixture
        return

    logger.info(f"Querying deps.dev BigQuery for top {top_n} {ecosystem} packages")
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
