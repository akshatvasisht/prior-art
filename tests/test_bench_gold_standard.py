"""Tests for bench.build_gold_standard — awesome-list → gold-standard records."""

from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from bench import build_gold_standard
from bench.build_gold_standard import (
    MIN_ENTRIES,
    _filter_applications,
    _parse_rewrite_response,
    parse_sections,
    rewrite_queries_via_llm,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("no payload")
        return self._payload


class _FakeClient:
    """Minimal httpx.Client stand-in routed via a {url -> response} mapping."""

    def __init__(self, mapping: dict[str, _FakeResponse], **kwargs):
        self._mapping = mapping
        self.calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url: str, **_kwargs) -> _FakeResponse:
        self.calls.append(url)
        if url in self._mapping:
            return self._mapping[url]
        return _FakeResponse(404)


@pytest.fixture
def cache_disabled(monkeypatch, tmp_path):
    """Reroute the on-disk cache so each test starts cold."""
    monkeypatch.setattr(
        build_gold_standard, "APP_FILTER_CACHE", tmp_path / ".app_filter_cache.json"
    )


def test_parse_sections_yields_categories_with_enough_entries():
    md = """
# Awesome Python

## HTTP Clients

- [requests](https://github.com/psf/requests) - HTTP for humans.
- [httpx](https://github.com/encode/httpx) - Next-gen HTTP.
- [urllib3](https://github.com/urllib3/urllib3) - Low-level HTTP.

## Tiny Section

- [only-one](https://github.com/foo/only-one) - Too small.

## JSON

* [orjson](https://github.com/ijl/orjson) - Fast JSON.
* [ujson](https://github.com/ultrajson/ujson) - Ultra JSON.
* [simplejson](https://github.com/simplejson/simplejson) - Simple JSON.
"""
    sections = list(parse_sections(md))

    headings = {h: r for h, r in sections}
    assert "HTTP Clients" in headings
    assert headings["HTTP Clients"] == ["requests", "httpx", "urllib3"]
    assert "JSON" in headings
    assert headings["JSON"] == ["orjson", "ujson", "simplejson"]
    # "Tiny Section" has only 1 entry (below MIN_ENTRIES) and should be dropped.
    assert "Tiny Section" not in headings
    assert MIN_ENTRIES == 3


def test_parse_sections_skips_non_github_bullets():
    md = """
## Routers

- [gin](https://github.com/gin-gonic/gin) - Gin router.
- [docs site](https://example.com/docs) - Not a github repo.
- [chi](https://github.com/go-chi/chi) - Another router.
- [mux](https://github.com/gorilla/mux) - Yet another.
"""
    sections = list(parse_sections(md))
    assert len(sections) == 1
    heading, repos = sections[0]
    assert heading == "Routers"
    assert repos == ["gin", "chi", "mux"]


def test_parse_sections_dedupes_within_category():
    md = """
## Clients

- [requests](https://github.com/psf/requests) - First.
- [httpx](https://github.com/encode/httpx) - Second.
- [requests](https://github.com/psf/requests) - Duplicate.
- [urllib3](https://github.com/urllib3/urllib3) - Third.
"""
    sections = list(parse_sections(md))
    _, repos = sections[0]
    assert repos == ["requests", "httpx", "urllib3"]


def test_parse_sections_skips_awesome_meta_repos():
    md = """
## Web Frameworks

- [django](https://github.com/django/django) - Real package.
- [awesome-django](https://github.com/wsvincent/awesome-django) - Meta-list.
- [flask](https://github.com/pallets/flask) - Real package.
- [Awesome-Flask](https://github.com/humiaozuzu/awesome-flask) - Meta-list, mixed case.
- [pyramid](https://github.com/Pylons/pyramid) - Real package.
"""
    sections = list(parse_sections(md))
    _, repos = sections[0]
    assert repos == ["django", "flask", "pyramid"]


def test_parse_sections_lowercases_repo_names():
    md = """
## Imaging

- [Pillow](https://github.com/python-pillow/Pillow) - PIL fork.
- [WeasyPrint](https://github.com/Kozea/WeasyPrint) - PDF renderer.
- [PyJWT](https://github.com/jpadilla/pyjwt) - JWT library.
"""
    sections = list(parse_sections(md))
    _, repos = sections[0]
    assert repos == ["pillow", "weasyprint", "pyjwt"]


# application filter — drop end-user CLI tools, games, etc. Library
# classifier is the unambiguous keep signal; absence + app classifier or
# -cli/-tool suffix is the drop signal. Fail-open on network errors.


def test_filter_applications_excludes_pypi_cli(cache_disabled):
    mapping = {
        # No "Topic :: Software Development :: Libraries" + Console env =>
        # high-confidence end-user app.
        "https://pypi.org/pypi/some-cli/json": _FakeResponse(
            200,
            {
                "info": {
                    "classifiers": [
                        "Environment :: Console",
                        "Topic :: System :: Shells",
                    ]
                }
            },
        ),
        "https://pypi.org/pypi/widget/json": _FakeResponse(
            200,
            {
                "info": {
                    "classifiers": [
                        "Topic :: Software Development :: Libraries :: Python Modules",
                    ]
                }
            },
        ),
    }
    fake = _FakeClient(mapping)
    with patch.object(httpx, "Client", lambda **k: fake):
        kept = _filter_applications(["some-cli", "widget"], "python")
    assert "widget" in kept
    assert "some-cli" not in kept


def test_filter_applications_keeps_pypi_library(cache_disabled):
    mapping = {
        "https://pypi.org/pypi/requests/json": _FakeResponse(
            200,
            {
                "info": {
                    "classifiers": [
                        "Topic :: Software Development :: Libraries",
                        "Topic :: Internet :: WWW/HTTP",
                    ]
                }
            },
        ),
    }
    fake = _FakeClient(mapping)
    with patch.object(httpx, "Client", lambda **k: fake):
        kept = _filter_applications(["requests"], "python")
    assert kept == ["requests"]


def test_filter_applications_excludes_crates_cli_utility(cache_disabled):
    mapping = {
        "https://crates.io/api/v1/crates/ripgrep": _FakeResponse(
            200,
            {"categories": [{"slug": "command-line-utilities"}]},
        ),
        "https://crates.io/api/v1/crates/serde": _FakeResponse(
            200,
            {"categories": [{"slug": "encoding"}]},
        ),
    }
    fake = _FakeClient(mapping)
    with patch.object(httpx, "Client", lambda **k: fake):
        kept = _filter_applications(["ripgrep", "serde"], "rust")
    assert "serde" in kept
    assert "ripgrep" not in kept


def test_filter_applications_skips_npm_and_go(cache_disabled):
    """npm/Go have no canonical app-vs-library signal — filter must no-op."""
    fake = _FakeClient({})
    with patch.object(httpx, "Client", lambda **k: fake):
        assert _filter_applications(["axios", "express"], "javascript") == [
            "axios",
            "express",
        ]
        assert _filter_applications(["gin", "chi"], "go") == ["gin", "chi"]
    # No HTTP calls were made for npm/Go.
    assert fake.calls == []


def test_filter_applications_fails_open_on_network_error(cache_disabled):
    class _BoomClient(_FakeClient):
        def get(self, url: str, **_kwargs):
            raise httpx.ConnectError("boom")

    fake = _BoomClient({})
    with patch.object(httpx, "Client", lambda **k: fake):
        # Network blew up → fail-open: keep both packages rather than emitting
        # a misleadingly empty gold-standard row.
        assert _filter_applications(["a", "b"], "python") == ["a", "b"]


def test_filter_applications_uses_cache_on_repeat(cache_disabled, tmp_path):
    """Second call must not re-fetch from the network — cache is the whole point."""
    seen_calls = 0

    class _CountingClient(_FakeClient):
        def get(self, url: str, **_kwargs):
            nonlocal seen_calls
            seen_calls += 1
            return _FakeResponse(
                200,
                {
                    "info": {
                        "classifiers": [
                            "Topic :: Software Development :: Libraries",
                        ]
                    }
                },
            )

    fake = _CountingClient({})
    with patch.object(httpx, "Client", lambda **k: fake):
        first = _filter_applications(["foo"], "python")
        second = _filter_applications(["foo"], "python")
    assert first == ["foo"]
    assert second == ["foo"]
    # Exactly one HTTP call: second invocation is served from the persisted cache.
    assert seen_calls == 1


# LLM query rewrites — Haiku-driven expansion of each category heading
# into 3 realistic developer queries, each inheriting the parent's grades.


def _write_input_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _make_anthropic_module(
    response_text: str | list[str],
    *,
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> MagicMock:
    """Build a fake `anthropic` module whose client returns the given response text(s).

    `response_text` may be a single string (used for every call) or a list (one per
    call, in order). The fake `usage` reports the supplied token counts so cost
    accumulation is testable.
    """
    texts = [response_text] if isinstance(response_text, str) else list(response_text)
    call_idx = {"i": 0}

    def _create(**_kwargs):
        idx = min(call_idx["i"], len(texts) - 1)
        call_idx["i"] += 1
        block = MagicMock()
        block.text = texts[idx]
        response = MagicMock()
        response.content = [block]
        response.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
        return response

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = _create

    fake_module = MagicMock()
    fake_module.Anthropic = MagicMock(return_value=fake_client)
    return fake_module


def test_parse_rewrite_response_handles_plain_array():
    assert _parse_rewrite_response('["a", "b", "c"]') == ["a", "b", "c"]


def test_parse_rewrite_response_strips_markdown_fence():
    assert _parse_rewrite_response('```json\n["x", "y", "z"]\n```') == ["x", "y", "z"]


def test_parse_rewrite_response_rejects_wrong_length():
    assert _parse_rewrite_response('["only-one"]') is None
    assert _parse_rewrite_response('["a", "b", "c", "d"]') is None


def test_parse_rewrite_response_rejects_non_json():
    assert _parse_rewrite_response("not json at all") is None


def test_rewrite_queries_skips_when_anthropic_not_installed(tmp_path, monkeypatch):
    """Lazy import is the whole point — missing SDK must exit cleanly, not raise."""
    inp = tmp_path / "in.jsonl"
    _write_input_jsonl(inp, [{"query": "http", "language": "python", "relevant_grades": {}}])
    out = tmp_path / "out.jsonl"

    # Force `import anthropic` inside the function to raise.
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    real_import = builtins.__import__

    def _fake_import(name, *a, **kw):
        if name == "anthropic":
            raise ImportError("simulated missing SDK")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    written = rewrite_queries_via_llm(inp, out)
    assert written == 0
    assert not out.exists()


def test_rewrite_queries_skips_when_api_key_missing(tmp_path, monkeypatch):
    inp = tmp_path / "in.jsonl"
    _write_input_jsonl(inp, [{"query": "http", "language": "python", "relevant_grades": {}}])
    out = tmp_path / "out.jsonl"

    fake = _make_anthropic_module('["a", "b", "c"]')
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    written = rewrite_queries_via_llm(inp, out)
    assert written == 0
    assert not out.exists()
    # Client was never even instantiated — we exit before touching anthropic.
    fake.Anthropic.assert_not_called()


def test_rewrite_queries_writes_three_per_input(tmp_path, monkeypatch):
    inp = tmp_path / "in.jsonl"
    grades = {"requests": 2, "httpx": 2, "urllib3": 1}
    _write_input_jsonl(
        inp,
        [
            {"query": "http clients", "language": "python", "relevant_grades": grades},
            {"query": "json", "language": "python", "relevant_grades": {"orjson": 2}},
        ],
    )
    out = tmp_path / "out.jsonl"

    fake = _make_anthropic_module(
        [
            '["python http library", "async http client", "requests alternative"]',
            '["fast json parser", "json serialization", "orjson alternative"]',
        ]
    )
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    written = rewrite_queries_via_llm(inp, out)
    assert written == 6  # 2 inputs × 3 rewrites

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 6
    # First three rows inherit the http grades + python language.
    for row in rows[:3]:
        assert row["language"] == "python"
        assert row["relevant_grades"] == grades
    assert [r["query"] for r in rows[:3]] == [
        "python http library",
        "async http client",
        "requests alternative",
    ]
    # Second three inherit the json grades.
    for row in rows[3:]:
        assert row["relevant_grades"] == {"orjson": 2}
    assert [r["query"] for r in rows[3:]] == [
        "fast json parser",
        "json serialization",
        "orjson alternative",
    ]


def test_rewrite_queries_handles_malformed_llm_response(tmp_path, monkeypatch):
    inp = tmp_path / "in.jsonl"
    _write_input_jsonl(
        inp,
        [
            {"query": "broken", "language": "python", "relevant_grades": {"a": 1}},
            {"query": "ok", "language": "python", "relevant_grades": {"b": 1}},
        ],
    )
    out = tmp_path / "out.jsonl"

    fake = _make_anthropic_module(
        [
            "this is not json",
            '["q1", "q2", "q3"]',
        ]
    )
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    written = rewrite_queries_via_llm(inp, out)
    # Malformed row skipped, second row succeeds → 3 outputs total.
    assert written == 3
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3
    assert all(r["relevant_grades"] == {"b": 1} for r in rows)


def test_rewrite_queries_respects_budget_cap(tmp_path, monkeypatch):
    """Tiny budget + heavy token usage → batch stops after the first call."""
    inp = tmp_path / "in.jsonl"
    _write_input_jsonl(
        inp,
        [
            {"query": "first", "language": "python", "relevant_grades": {"a": 1}},
            {"query": "second", "language": "python", "relevant_grades": {"b": 1}},
            {"query": "third", "language": "python", "relevant_grades": {"c": 1}},
        ],
    )
    out = tmp_path / "out.jsonl"

    # Massive token usage so the first call alone blows past a 0.001 budget.
    fake = _make_anthropic_module(
        '["q1", "q2", "q3"]',
        input_tokens=10_000_000,
        output_tokens=10_000_000,
    )
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    written = rewrite_queries_via_llm(inp, out, budget_usd=0.001)
    # First call lands before the cap check, but the second is gated out.
    assert written == 3
    assert fake.Anthropic.return_value.messages.create.call_count == 1
