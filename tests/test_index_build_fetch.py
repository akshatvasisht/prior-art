"""Tests for scripts.index_build.fetch — snapshot popularity + live recency."""

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


def test_fetch_ecosystem_dedupes_across_popularity_and_recency():
    """Recency lane records that overlap the snapshot slice must be dropped."""
    from scripts.index_build import fetch as fetch_mod

    popularity = [
        {"name": "requests", "registry": "pypi", "description": "HTTP", "github_url": None},
        {"name": "httpx", "registry": "pypi", "description": "HTTP async", "github_url": None},
    ]
    recency = [
        # Duplicate with popularity — should be dropped.
        {"name": "requests", "registry": "pypi", "description": "HTTP", "github_url": None},
        # Fresh entry — should be kept.
        {"name": "niquests", "registry": "pypi", "description": "HTTP 2+3", "github_url": None},
    ]

    with patch.object(fetch_mod, "_iter_popular_snapshot", return_value=iter(popularity)):
        with patch.object(fetch_mod, "_iter_recent_ms", return_value=iter(recency)):
            results = list(fetch_mod.fetch_ecosystem("python", top_n=10, recency_n=10))

    names = [r["name"] for r in results]
    assert names == ["requests", "httpx", "niquests"]


def test_fetch_ecosystem_recency_disabled_when_zero():
    from scripts.index_build import fetch as fetch_mod

    popularity = [
        {"name": "requests", "registry": "pypi", "description": "HTTP", "github_url": None}
    ]

    with patch.object(fetch_mod, "_iter_popular_snapshot", return_value=iter(popularity)):
        with patch.object(fetch_mod, "_iter_recent_ms") as mock_recency:
            results = list(fetch_mod.fetch_ecosystem("python", top_n=10, recency_n=0))

    assert len(results) == 1
    mock_recency.assert_not_called()


def test_iter_popular_snapshot_sorts_by_dependent_packages_count(tmp_path: Path):
    """Python pulls top-N by dependent_packages_count in descending order."""
    from scripts.index_build import fetch as fetch_mod

    rows = [
        {"name": "low", "description": "tail", "dependent_packages_count": 5, "downloads": 100},
        {"name": "top", "description": "winner", "dependent_packages_count": 99, "downloads": 1},
        {"name": "mid", "description": "middle", "dependent_packages_count": 50, "downloads": 50},
    ]
    snapshot = tmp_path / "python.jsonl"
    snapshot.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    with patch("huggingface_hub.hf_hub_download", return_value=str(snapshot)):
        result = list(fetch_mod._iter_popular_snapshot("python", top_n=2))

    assert [r["name"] for r in result] == ["top", "mid"]
    assert result[0]["registry"] == "pypi"


def test_iter_popular_snapshot_uses_downloads_for_npm(tmp_path: Path):
    """npm's popularity_key is downloads, not dependent_packages_count."""
    from scripts.index_build import fetch as fetch_mod

    rows = [
        {"name": "by-deps", "description": "x", "dependent_packages_count": 999, "downloads": 1},
        {
            "name": "by-dl",
            "description": "y",
            "dependent_packages_count": 0,
            "downloads": 1_000_000,
        },
    ]
    snapshot = tmp_path / "npm.jsonl"
    snapshot.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    with patch("huggingface_hub.hf_hub_download", return_value=str(snapshot)):
        result = list(fetch_mod._iter_popular_snapshot("npm", top_n=10))

    assert [r["name"] for r in result] == ["by-dl", "by-deps"]


def test_iter_popular_snapshot_skips_blank_names_and_falls_back_for_blank_desc(tmp_path: Path):
    from scripts.index_build import fetch as fetch_mod

    rows = [
        {"name": "", "description": "no name", "dependent_packages_count": 100},
        {"name": "  ", "description": "whitespace only", "dependent_packages_count": 50},
        {"name": "real-pkg", "description": "", "dependent_packages_count": 10},
        {"name": "no-desc", "description": None, "dependent_packages_count": 5},
    ]
    snapshot = tmp_path / "python.jsonl"
    # Include a blank line to exercise the parser's skip-blank path.
    body = "\n".join(json.dumps(r) for r in rows) + "\n\n"
    snapshot.write_text(body, encoding="utf-8")

    with patch("huggingface_hub.hf_hub_download", return_value=str(snapshot)):
        result = list(fetch_mod._iter_popular_snapshot("python", top_n=10))

    assert [r["name"] for r in result] == ["real-pkg", "no-desc"]
    assert result[0]["description"] == "real-pkg"
    assert result[1]["description"] == "no-desc"


def test_iter_recent_ms_paginates_until_top_n(monkeypatch: pytest.MonkeyPatch):
    from scripts.index_build import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "PER_PAGE", 2)

    pages = [
        [
            {"name": "a", "description": "first", "repository_url": "u1"},
            {"name": "b", "description": "second", "repository_url": None},
        ],
        [
            {"name": "c", "description": "third", "repository_url": None},
            {"name": "d", "description": "fourth", "repository_url": None},
        ],
    ]
    calls: list[int] = []

    def _fake_get(_client, _url, params):
        page_idx = params["page"] - 1
        calls.append(params["page"])
        return pages[page_idx] if page_idx < len(pages) else []

    monkeypatch.setattr(fetch_mod, "_get_with_retry", _fake_get)

    result = list(fetch_mod._iter_recent_ms("python", top_n=3))
    assert [r["name"] for r in result] == ["a", "b", "c"]
    # top_n=3 caps within page 2, so we should not have requested page 3.
    assert calls == [1, 2]


def test_iter_recent_ms_stops_on_partial_page(monkeypatch: pytest.MonkeyPatch):
    """A short page (less than per_page rows) signals end-of-data."""
    from scripts.index_build import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "PER_PAGE", 5)

    def _fake_get(_client, _url, _params):
        return [{"name": "only", "description": "x", "repository_url": None}]

    monkeypatch.setattr(fetch_mod, "_get_with_retry", _fake_get)

    result = list(fetch_mod._iter_recent_ms("python", top_n=20))
    assert len(result) == 1


def test_iter_recent_ms_skips_blank_names(monkeypatch: pytest.MonkeyPatch):
    from scripts.index_build import fetch as fetch_mod

    def _fake_get(_client, _url, _params):
        return [
            {"name": "", "description": "no name", "repository_url": None},
            {"name": "real", "description": "", "repository_url": None},
        ]

    monkeypatch.setattr(fetch_mod, "_get_with_retry", _fake_get)

    # Force termination after first page by making the next call return [].
    state = {"called": False}

    def _fake_get_then_empty(client, url, params):
        if state["called"]:
            return []
        state["called"] = True
        return _fake_get(client, url, params)

    monkeypatch.setattr(fetch_mod, "_get_with_retry", _fake_get_then_empty)

    result = list(fetch_mod._iter_recent_ms("python", top_n=10))
    assert [r["name"] for r in result] == ["real"]
    assert result[0]["description"] == "real"


def test_iter_recent_ms_handles_api_failure(monkeypatch: pytest.MonkeyPatch):
    """A failed ecosyste.ms response should yield no records, not crash."""
    from scripts.index_build import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "_get_with_retry", lambda *_a, **_kw: None)

    result = list(fetch_mod._iter_recent_ms("python", top_n=10))
    assert result == []


def test_get_with_retry_returns_data_on_200(monkeypatch: pytest.MonkeyPatch):
    from scripts.index_build import fetch as fetch_mod

    class _Resp:
        status_code = 200

        def json(self):
            return [{"name": "x"}]

    class _Client:
        def get(self, _url, params=None):
            return _Resp()

    monkeypatch.setattr(fetch_mod.time, "sleep", lambda _s: None)
    result = fetch_mod._get_with_retry(_Client(), "u", {})
    assert result == [{"name": "x"}]


def test_get_with_retry_returns_none_when_payload_is_not_list(monkeypatch: pytest.MonkeyPatch):
    from scripts.index_build import fetch as fetch_mod

    class _Resp:
        status_code = 200

        def json(self):
            return {"not": "a list"}

    class _Client:
        def get(self, _url, params=None):
            return _Resp()

    monkeypatch.setattr(fetch_mod.time, "sleep", lambda _s: None)
    assert fetch_mod._get_with_retry(_Client(), "u", {}) is None


def test_get_with_retry_returns_none_on_non_retriable_status(monkeypatch: pytest.MonkeyPatch):
    from scripts.index_build import fetch as fetch_mod

    class _Resp:
        status_code = 404
        text = "not found"

    class _Client:
        def get(self, _url, params=None):
            return _Resp()

    monkeypatch.setattr(fetch_mod.time, "sleep", lambda _s: None)
    assert fetch_mod._get_with_retry(_Client(), "u", {}) is None


def test_get_with_retry_exhausts_on_5xx(monkeypatch: pytest.MonkeyPatch):
    from scripts.index_build import fetch as fetch_mod

    class _Resp:
        status_code = 503
        text = "down"

    class _Client:
        def get(self, _url, params=None):
            return _Resp()

    monkeypatch.setattr(fetch_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(fetch_mod, "MAX_RETRIES", 2)
    assert fetch_mod._get_with_retry(_Client(), "u", {}) is None


def test_get_with_retry_swallows_request_error(monkeypatch: pytest.MonkeyPatch):
    from scripts.index_build import fetch as fetch_mod

    class _Client:
        def get(self, _url, params=None):
            raise fetch_mod.httpx.RequestError("boom")

    monkeypatch.setattr(fetch_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(fetch_mod, "MAX_RETRIES", 2)
    assert fetch_mod._get_with_retry(_Client(), "u", {}) is None
