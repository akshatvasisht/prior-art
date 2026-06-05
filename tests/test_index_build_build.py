"""Tests for scripts.index_build.build — focused on per-ecosystem top_n resolution.

The actual shard build path (fastembed + usearch) is exercised by integration
tests + CI workflow runs; these unit tests pin the cap-resolution contract so
The per-ecosystem calibration doesn't silently regress.
"""

from __future__ import annotations

import pytest

from scripts.index_build import build


def test_default_top_n_covers_all_supported_ecosystems():
    """Every ecosystem priorart actually ships must have an explicit cap."""
    expected = {"npm", "crates", "python", "go", "maven", "nuget"}
    assert expected <= set(build.DEFAULT_TOP_N), (
        "DEFAULT_TOP_N must include all six supported ecosystems"
    )


@pytest.mark.parametrize(
    "ecosystem,expected",
    [
        ("npm", 50_000),
        ("crates", 12_000),
        ("python", 15_000),
        ("go", 20_000),
    ],
)
def test_resolve_top_n_returns_per_ecosystem_default(ecosystem, expected):
    """Calibrated per-ecosystem caps from the reverse-dependency analysis."""
    assert build._resolve_top_n(ecosystem, override=None) == expected


def test_resolve_top_n_honors_explicit_override():
    """Passing an int override wins over the per-ecosystem default."""
    assert build._resolve_top_n("npm", override=100) == 100
    assert build._resolve_top_n("crates", override=5000) == 5000


def test_resolve_top_n_rejects_non_positive_override():
    """A zero or negative --top-n is a user error, not a silent no-op: a
    negative cap would slice the popularity list as rows[:-n] and quietly index
    the wrong packages, and 0 would fetch nothing."""
    for bad in (0, -1, -20_000):
        for ecosystem in ("npm", "ruby"):  # known + unknown ecosystem
            with pytest.raises(ValueError):
                build._resolve_top_n(ecosystem, override=bad)


def test_resolve_top_n_falls_back_for_unknown_ecosystem():
    """Unknown ecosystems use FALLBACK_TOP_N rather than raising — keeps the
    build script tolerant of new ecosystems added before DEFAULT_TOP_N is
    updated, at the cost of a generic 20K cap until calibrated."""
    assert build._resolve_top_n("ruby", override=None) == build.FALLBACK_TOP_N
