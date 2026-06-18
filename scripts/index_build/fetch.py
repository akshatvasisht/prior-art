"""
Fetch per-ecosystem package metadata for the priorart semantic index.

Single-source (snapshot) design:

- **Popularity lane** (top-N by ``dependent_packages_count`` or ``downloads``):
  reads slim per-ecosystem JSONL from the ``priorart/package-snapshot`` HF Hub
  dataset. The snapshot is produced by ``extract-snapshot.yml`` from
  ecosyste.ms's quarterly PostgreSQL dump, which avoids the deep counter-column
  ORDER BY queries that time out on the live API. The snapshot refreshes
  weekly, so it already captures newly-prominent packages.

Each fetcher yields dicts with::

    {"name": str, "registry": str, "description": str,
     "github_url": str | None, "popularity": int}

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

logger = logging.getLogger(__name__)


def _hf_download_with_retry(*, attempts: int = 6, base_delay: float = 15.0, **kwargs) -> str:
    """``hf_hub_download`` with exponential backoff on transient Hub errors.

    Secondary guard behind huggingface_hub's native handling, which does **not**
    honor a 429 ``Retry-After`` (huggingface/huggingface_hub#2360), so a
    rate-limit burst on the largest snapshot can still surface as a fatal error.
    Retry with a wide backoff + jitter (pair with ``HF_HUB_DOWNLOAD_TIMEOUT`` in
    CI). The 2026-06 npm-shard 429 exhausted the prior 3x/5s in ~30s and needed a
    manual re-trigger; 6x/15s rides out a multi-minute rate-limit window.
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


SNAPSHOT_REPO_ID = "priorart/package-snapshot"

# Per-ecosystem popularity sort key + registry label. The popularity lane reads
# the snapshot dataset. npm uses ``downloads`` because dependent-count
# under-ranks npm-specific tooling that ships as standalone CLIs rather than
# libraries others depend on.
ECOSYSTEM_CONFIG = {
    "python": {"popularity_key": "dependent_packages_count", "registry": "pypi"},
    "npm": {"popularity_key": "downloads", "registry": "npm"},
    "crates": {"popularity_key": "dependent_packages_count", "registry": "cargo"},
    "go": {"popularity_key": "dependent_packages_count", "registry": "go"},
    "maven": {"popularity_key": "dependent_packages_count", "registry": "maven"},
    "nuget": {"popularity_key": "dependent_packages_count", "registry": "nuget"},
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


# Hard cap on long-tail inclusion, as a multiple of top_n, to bound shard size
# when a long_tail_floor is configured.
_LONG_TAIL_CAP_MULTIPLIER = 5


def _select_snapshot_rows(
    rows: list[dict], sort_key: str, top_n: int, long_tail_floor: float | None = None
) -> list[dict]:
    """Select rows for the shard, ranked by entrenchment (``sort_key`` desc).

    Default (``long_tail_floor`` None): the top-``top_n`` head only. When a floor
    is set, also keep any lower-ranked package still at/above it — niche-but-real
    packages that the popularity cliff would otherwise drop, the #1 demo-coverage
    risk (OPEN_ISSUES A22) — capped at ``top_n * _LONG_TAIL_CAP_MULTIPLIER`` to
    bound shard size.
    """
    ranked = sorted(rows, key=lambda r: r.get(sort_key) or 0, reverse=True)
    if long_tail_floor is None:
        return ranked[:top_n]
    cap = top_n * _LONG_TAIL_CAP_MULTIPLIER
    return [
        r
        for i, r in enumerate(ranked[:cap])
        if i < top_n or (r.get(sort_key) or 0) >= long_tail_floor
    ]


def _iter_popular_snapshot(ecosystem: str, top_n: int) -> Iterator[dict]:
    """Yield records from the HF Hub snapshot, ranked by entrenchment.

    Yields the top-``top_n`` head, plus the long tail above a per-ecosystem
    ``long_tail_floor`` when one is configured (off by default).
    """
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

    selected = _select_snapshot_rows(rows, sort_key, top_n, cfg.get("long_tail_floor"))
    logger.info(
        "snapshot %s: %d rows; yielding %d by %s",
        ecosystem,
        len(rows),
        len(selected),
        sort_key,
    )

    for row in selected:
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
            # Raw entrenchment signal behind the ranking (downloads for npm,
            # dependent-package count elsewhere). Carried into the shard
            # metadata so retrieval can apply a popularity prior. Scales differ
            # across ecosystems, so the consumer log-normalizes per shard.
            "popularity": row.get(sort_key) or 0,
        }


def fetch_ecosystem(ecosystem: str, top_n: int = 20_000) -> Iterator[dict]:
    """Yield package records for ``ecosystem``.

    Prefers a local fixture if ``PRIORART_INDEX_FIXTURE`` is set; otherwise
    reads the top-``top_n`` popularity slice from the HF Hub snapshot. Records
    are deduped by ``(name, registry)``.
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
