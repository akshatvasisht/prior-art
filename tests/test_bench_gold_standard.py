"""Tests for bench.build_gold_standard — awesome-list → gold-standard records."""

from bench.build_gold_standard import MIN_ENTRIES, parse_sections


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
