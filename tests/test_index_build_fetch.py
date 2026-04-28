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

    with patch.object(fetch_mod, "_warm_cache"):
        with patch.object(fetch_mod, "_iter_deps_dev", return_value=iter(primary)):
            with patch.object(fetch_mod, "_iter_deps_dev_by_recency", return_value=iter(recency)):
                results = list(fetch_mod.fetch_ecosystem("python", top_n=10, recency_n=10))

    names = [r["name"] for r in results]
    assert names == ["requests", "httpx", "niquests"]


def test_fetch_ecosystem_recency_disabled_when_zero():
    from scripts.index_build import fetch as fetch_mod

    primary = [{"name": "requests", "registry": "pypi", "description": "HTTP", "github_url": None}]

    with patch.object(fetch_mod, "_warm_cache"):
        with patch.object(fetch_mod, "_iter_deps_dev", return_value=iter(primary)):
            with patch.object(fetch_mod, "_iter_deps_dev_by_recency") as mock_recency:
                results = list(fetch_mod.fetch_ecosystem("python", top_n=10, recency_n=0))

    assert len(results) == 1
    mock_recency.assert_not_called()


def test_iter_deps_dev_by_recency_handles_api_failure(monkeypatch):
    """A failed ecosyste.ms response should yield no records, not crash."""
    import scripts.index_build.fetch as fetch_mod

    def _fail(*_args, **_kwargs):
        return None

    monkeypatch.setattr(fetch_mod, "_get_with_retry", _fail)

    records = list(fetch_mod._iter_deps_dev_by_recency("python", top_n=10))
    assert records == []


def test_warm_cache_skipped_via_env(monkeypatch):
    """PRIORART_SKIP_WARMUP=1 must short-circuit before any HTTP traffic."""
    import scripts.index_build.fetch as fetch_mod

    monkeypatch.setenv("PRIORART_SKIP_WARMUP", "1")
    with patch.object(fetch_mod.httpx, "Client") as mock_client:
        fetch_mod._warm_cache("python", top_n=20000, recency_n=2000)
    mock_client.assert_not_called()


def test_warm_cache_visits_each_page_and_swallows_errors(monkeypatch):
    """Warm-up must request every (sort, page) combo and ignore failures."""
    import scripts.index_build.fetch as fetch_mod

    monkeypatch.delenv("PRIORART_SKIP_WARMUP", raising=False)
    monkeypatch.setattr(fetch_mod, "WARMUP_PAUSE_SECONDS", 0)

    requested: list[tuple] = []

    class _Elapsed:
        def total_seconds(self):
            return 0.0

    class _FakeResp:
        status_code = 500
        elapsed = _Elapsed()

    class _FakeClient:
        def __init__(self, *_, **__):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def get(self, url, params):
            requested.append((url, params["sort"], params["page"]))
            # Mix 500s and a network error to verify both are swallowed.
            if params["page"] == 2:
                raise fetch_mod.httpx.RequestError("boom")
            return _FakeResp()

    monkeypatch.setattr(fetch_mod.httpx, "Client", _FakeClient)

    fetch_mod._warm_cache("python", top_n=2500, recency_n=1500)

    # top_n=2500 → ceil(2500/1000)=3 popularity pages.
    # recency_n=1500 → ceil(1500/1000)=2 recency pages.
    sort_pages = [(s, p) for (_url, s, p) in requested]
    assert ("dependent_packages_count", 1) in sort_pages
    assert ("dependent_packages_count", 2) in sort_pages
    assert ("dependent_packages_count", 3) in sort_pages
    assert ("latest_release_published_at", 1) in sort_pages
    assert ("latest_release_published_at", 2) in sort_pages
    assert len(sort_pages) == 5
