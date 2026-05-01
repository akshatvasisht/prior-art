"""Tests for scripts.index_build.extract_dump — pg_restore COPY-stream parser."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest


def _stream(*lines: str) -> io.StringIO:
    """Build a stdin-like stream from individual lines (newline-terminated)."""
    return io.StringIO("\n".join(lines) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_extract_picks_packages_block_and_skips_others(tmp_path: Path):
    from scripts.index_build.extract_dump import extract

    stream = _stream(
        "-- comment line",
        "SET client_encoding = 'UTF8';",
        "COPY public.dependencies (id, package_id, kind) FROM stdin;",
        "1\t10\truntime",
        "2\t20\tdev",
        "\\.",
        "",
        "COPY public.packages (id, name, ecosystem, description, status, downloads, dependent_packages_count, dependent_repos_count, repository_url, homepage, latest_release_published_at) FROM stdin;",
        "1\trequests\tpypi\tHTTP for humans\tactive\t50000000\t12000\t800\thttps://github.com/psf/requests\thttps://requests.readthedocs.io\t2024-01-15 10:00:00",
        "2\tlodash\tnpm\tutility\tactive\t80000000\t5000\t300\thttps://github.com/lodash/lodash\t\\N\t2023-12-01 12:00:00",
        "\\.",
    )

    counts = extract(stream, tmp_path)
    assert counts["python"] == 1
    assert counts["npm"] == 1
    assert counts["crates"] == 0

    py = _read_jsonl(tmp_path / "python.jsonl")
    assert py[0]["name"] == "requests"
    assert py[0]["registry"] == "pypi"
    assert py[0]["downloads"] == 50_000_000
    assert py[0]["repository_url"] == "https://github.com/psf/requests"

    npm = _read_jsonl(tmp_path / "npm.jsonl")
    assert npm[0]["name"] == "lodash"
    assert npm[0]["homepage"] is None  # \N → null


def test_extract_filters_inactive_status(tmp_path: Path):
    from scripts.index_build.extract_dump import extract

    stream = _stream(
        "COPY public.packages (id, name, ecosystem, status, downloads, dependent_packages_count, dependent_repos_count) FROM stdin;",
        "1\tabandoned\tpypi\tremoved\t100\t0\t0",
        "2\tlive\tpypi\tactive\t200\t1\t0",
        "\\.",
    )
    extract(stream, tmp_path)
    py = _read_jsonl(tmp_path / "python.jsonl")
    assert [r["name"] for r in py] == ["live"]


def test_extract_keeps_null_and_empty_status(tmp_path: Path):
    """Pre-default rows store status as NULL; they must survive the filter."""
    from scripts.index_build.extract_dump import extract

    stream = _stream(
        "COPY public.packages (id, name, ecosystem, status, downloads, dependent_packages_count, dependent_repos_count) FROM stdin;",
        "1\tnullstatus\tpypi\t\\N\t100\t1\t0",
        "2\temptystatus\tpypi\t\t100\t1\t0",
        "3\twithdrawn\tpypi\twithdrawn\t100\t1\t0",
        "\\.",
    )
    extract(stream, tmp_path)
    py = _read_jsonl(tmp_path / "python.jsonl")
    assert [r["name"] for r in py] == ["nullstatus", "emptystatus"]


def test_extract_aborts_when_zero_matches_after_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """If the filter rejects every row up to EARLY_EXIT_THRESHOLD, fail fast."""
    from scripts.index_build import extract_dump as mod

    monkeypatch.setattr(mod, "EARLY_EXIT_THRESHOLD", 5)
    rows = [f"{i}\tpkg{i}\trubygems\tactive\t1\t0\t0" for i in range(10)]
    stream = _stream(
        "COPY public.packages (id, name, ecosystem, status, downloads, dependent_packages_count, dependent_repos_count) FROM stdin;",
        *rows,
        "\\.",
    )
    with pytest.raises(RuntimeError, match="filter logic is broken"):
        mod.extract(stream, tmp_path)


def test_extract_filters_unknown_ecosystem(tmp_path: Path):
    from scripts.index_build.extract_dump import extract

    stream = _stream(
        "COPY public.packages (id, name, ecosystem, status, downloads, dependent_packages_count, dependent_repos_count) FROM stdin;",
        "1\trubypkg\trubygems\tactive\t100\t1\t0",
        "2\trealpkg\tpypi\tactive\t200\t1\t0",
        "\\.",
    )
    extract(stream, tmp_path)
    assert (tmp_path / "python.jsonl").exists()
    assert not (tmp_path / "rubygems.jsonl").exists()


def test_extract_unescapes_copy_format(tmp_path: Path):
    """Postgres COPY escape sequences must roundtrip through the parser."""
    from scripts.index_build.extract_dump import extract

    # Field 4 has: literal backslash (\\) + literal `t` + tab character (\t)
    # → after unescape: backslash + t + TAB.
    stream = _stream(
        "COPY public.packages (id, name, ecosystem, description, status, downloads, dependent_packages_count, dependent_repos_count) FROM stdin;",
        "1\twith-newline\tpypi\tline1\\nline2\tactive\t1\t1\t0",
        "2\twith-tab-and-bs\tpypi\t\\\\t\\tend\tactive\t1\t1\t0",
        "\\.",
    )
    extract(stream, tmp_path)
    py = _read_jsonl(tmp_path / "python.jsonl")
    assert py[0]["description"] == "line1\nline2"
    assert py[1]["description"] == "\\t\tend"


def test_extract_handles_null_numeric_fields(tmp_path: Path):
    """\\N for numeric counter fields must fall back to 0."""
    from scripts.index_build.extract_dump import extract

    stream = _stream(
        "COPY public.packages (id, name, ecosystem, description, status, downloads, dependent_packages_count, dependent_repos_count) FROM stdin;",
        "1\tnewbie\tpypi\tjust-uploaded\tactive\t\\N\t\\N\t\\N",
        "\\.",
    )
    extract(stream, tmp_path)
    py = _read_jsonl(tmp_path / "python.jsonl")
    assert py[0]["downloads"] == 0
    assert py[0]["dependent_packages_count"] == 0


def test_extract_skips_malformed_row(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """Column-count mismatch should warn and skip, not crash."""
    from scripts.index_build.extract_dump import extract

    stream = _stream(
        "COPY public.packages (id, name, ecosystem, status, downloads, dependent_packages_count, dependent_repos_count) FROM stdin;",
        "1\tgood\tpypi\tactive\t100\t1\t0",
        "2\tbroken-row-too-few-cols",
        "3\talsogood\tpypi\tactive\t200\t2\t0",
        "\\.",
    )
    with caplog.at_level("WARNING"):
        extract(stream, tmp_path)
    py = _read_jsonl(tmp_path / "python.jsonl")
    assert [r["name"] for r in py] == ["good", "alsogood"]
    assert any("malformed row" in r.message for r in caplog.records)


def test_extract_raises_when_no_packages_block_found(tmp_path: Path):
    from scripts.index_build.extract_dump import extract

    stream = _stream(
        "SET search_path = public;",
        "COPY public.dependencies (id, package_id) FROM stdin;",
        "1\t10",
        "\\.",
    )
    with pytest.raises(RuntimeError, match="no public.packages COPY block"):
        extract(stream, tmp_path)


def test_extract_handles_garbage_integer_fields(tmp_path: Path):
    """Non-numeric strings in counter fields must coerce to 0, not crash."""
    from scripts.index_build.extract_dump import extract

    stream = _stream(
        "COPY public.packages (id, name, ecosystem, status, downloads, dependent_packages_count, dependent_repos_count) FROM stdin;",
        "1\twonky\tpypi\tactive\tabc\txyz\t0",
        "\\.",
    )
    extract(stream, tmp_path)
    py = _read_jsonl(tmp_path / "python.jsonl")
    assert py[0]["downloads"] == 0
    assert py[0]["dependent_packages_count"] == 0


def test_main_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    """The CLI entry point reads stdin and writes to argv[1]."""
    import sys

    from scripts.index_build import extract_dump as mod

    out = tmp_path / "out"
    monkeypatch.setattr(
        sys,
        "argv",
        ["extract_dump", str(out)],
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        _stream(
            "COPY public.packages (id, name, ecosystem, status, downloads, dependent_packages_count, dependent_repos_count) FROM stdin;",
            "1\tcli-pkg\tpypi\tactive\t1\t1\t0",
            "\\.",
        ),
    )
    mod.main()
    py = _read_jsonl(out / "python.jsonl")
    assert py[0]["name"] == "cli-pkg"
