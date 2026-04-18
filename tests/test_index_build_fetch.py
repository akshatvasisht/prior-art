"""Tests for scripts.index_build.fetch — ecosystem fetcher dedup + fallback."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def fixture_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point PRIORART_INDEX_FIXTURE at a temp dir; return it."""
    monkeypatch.setenv("PRIORART_INDEX_FIXTURE", str(tmp_path))
    return tmp_path


def test_fetch_ecosystem_uses_fixture_when_present(fixture_dir: Path):
    from scripts.index_build.fetch import fetch_ecosystem

    (fixture_dir / "python.jsonl").write_text(
        json.dumps(
            {"name": "requests", "registry": "pypi", "description": "HTTP", "github_url": None}
        )
        + "\n",
        encoding="utf-8",
    )

    records = list(fetch_ecosystem("python"))
    assert len(records) == 1
    assert records[0]["name"] == "requests"


def test_fetch_ecosystem_dedupes_across_primary_and_recency():
    """Recency lane records that overlap the primary slice must be dropped."""
    from scripts.index_build import fetch as fetch_mod

    primary = [
        {"name": "requests", "registry": "pypi", "description": "HTTP", "github_url": None},
        {"name": "httpx", "registry": "pypi", "description": "HTTP async", "github_url": None},
    ]
    recency = [
        # Duplicate with primary — should be dropped.
        {"name": "requests", "registry": "pypi", "description": "HTTP", "github_url": None},
        # Fresh entry — should be kept.
        {"name": "niquests", "registry": "pypi", "description": "HTTP 2+3", "github_url": None},
    ]

    with patch.object(fetch_mod, "_iter_deps_dev", return_value=iter(primary)):
        with patch.object(fetch_mod, "_iter_deps_dev_by_recency", return_value=iter(recency)):
            results = list(fetch_mod.fetch_ecosystem("python", top_n=10, recency_n=10))

    names = [r["name"] for r in results]
    assert names == ["requests", "httpx", "niquests"]


def test_fetch_ecosystem_recency_disabled_when_zero():
    from scripts.index_build import fetch as fetch_mod

    primary = [{"name": "requests", "registry": "pypi", "description": "HTTP", "github_url": None}]

    with patch.object(fetch_mod, "_iter_deps_dev", return_value=iter(primary)):
        with patch.object(fetch_mod, "_iter_deps_dev_by_recency") as mock_recency:
            results = list(fetch_mod.fetch_ecosystem("python", top_n=10, recency_n=0))

    assert len(results) == 1
    mock_recency.assert_not_called()


def test_iter_deps_dev_by_recency_handles_missing_bigquery_lib(monkeypatch):
    """Missing google-cloud-bigquery should yield no records, not crash."""
    import sys

    import scripts.index_build.fetch as fetch_mod

    # Force the deferred import to fail.
    monkeypatch.setitem(sys.modules, "google.cloud", None)

    records = list(fetch_mod._iter_deps_dev_by_recency("python", top_n=10))
    assert records == []
