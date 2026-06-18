"""Tests for scripts.index_build.fetch — snapshot popularity lane."""

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


def test_fetch_ecosystem_yields_snapshot_records_and_dedupes():
    """fetch_ecosystem(eco, top_n=...) yields only snapshot-lane records, deduped."""
    from scripts.index_build import fetch as fetch_mod

    popularity = [
        {"name": "requests", "registry": "pypi", "description": "HTTP", "github_url": None},
        {"name": "httpx", "registry": "pypi", "description": "HTTP async", "github_url": None},
        # Duplicate (name, registry) — should be dropped.
        {"name": "requests", "registry": "pypi", "description": "HTTP", "github_url": None},
    ]

    captured: dict = {}

    def _fake_snapshot(eco, top_n):
        captured["top_n"] = top_n
        return iter(popularity)

    with patch.object(fetch_mod, "_iter_popular_snapshot", side_effect=_fake_snapshot):
        results = list(fetch_mod.fetch_ecosystem("python", top_n=10))

    assert [r["name"] for r in results] == ["requests", "httpx"]
    assert captured["top_n"] == 10


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


def test_hf_download_with_retry_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch):
    """Transient failures are retried with backoff before succeeding."""
    import huggingface_hub

    from scripts.index_build import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod.time, "sleep", lambda _s: None)

    calls = {"n": 0}

    def _flaky(**_kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("429 rate limited")
        return "/tmp/snapshot.jsonl"

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _flaky)

    result = fetch_mod._hf_download_with_retry(repo_id="r", filename="f", repo_type="dataset")
    assert result == "/tmp/snapshot.jsonl"
    assert calls["n"] == 2


def test_hf_download_with_retry_raises_after_exhaustion(monkeypatch: pytest.MonkeyPatch):
    """Default attempts is the secondary-guard value of 6; it raises when exhausted."""
    import huggingface_hub

    from scripts.index_build import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod.time, "sleep", lambda _s: None)

    calls = {"n": 0}

    def _always_fail(**_kwargs):
        calls["n"] += 1
        raise RuntimeError("429 rate limited")

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _always_fail)

    with pytest.raises(RuntimeError):
        fetch_mod._hf_download_with_retry(repo_id="r", filename="f", repo_type="dataset")
    assert calls["n"] == 6


# --- _select_snapshot_rows (long-tail coverage) ---


def test_select_snapshot_rows_top_n_only():
    from scripts.index_build.fetch import _select_snapshot_rows

    rows = [{"p": 5}, {"p": 1}, {"p": 9}, {"p": 3}]
    out = _select_snapshot_rows(rows, "p", top_n=2)
    assert [r["p"] for r in out] == [9, 5]  # top 2 by p desc, no long tail


def test_select_snapshot_rows_long_tail_floor():
    from scripts.index_build.fetch import _select_snapshot_rows

    rows = [{"p": 100}, {"p": 80}, {"p": 60}, {"p": 40}, {"p": 10}]
    # top_n=2 head (100, 80) + long tail at/above 50 (60); 40 and 10 dropped.
    out = _select_snapshot_rows(rows, "p", top_n=2, long_tail_floor=50)
    assert [r["p"] for r in out] == [100, 80, 60]


def test_select_snapshot_rows_long_tail_capped():
    from scripts.index_build.fetch import _LONG_TAIL_CAP_MULTIPLIER, _select_snapshot_rows

    # All rows clear the floor, but selection is capped at top_n * multiplier.
    rows = [{"p": 100 - i} for i in range(50)]
    out = _select_snapshot_rows(rows, "p", top_n=3, long_tail_floor=0)
    assert len(out) == 3 * _LONG_TAIL_CAP_MULTIPLIER


def test_select_snapshot_rows_missing_sort_key_treated_as_zero():
    from scripts.index_build.fetch import _select_snapshot_rows

    rows = [{"p": 5}, {}, {"p": 9}]  # one row missing the sort key
    out = _select_snapshot_rows(rows, "p", top_n=3, long_tail_floor=1)
    # 9 and 5 clear the floor; the keyless row (treated as 0) is below it but
    # still inside the top_n head, so it survives.
    assert [r.get("p", 0) for r in out] == [9, 5, 0]
