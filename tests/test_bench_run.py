"""Tests for bench.run — canonical naming, reproducibility preamble, per-language stratification."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bench import run as bench_run

# defense-in-depth: even if retrieval.py forgets Go path-tail normalization,
# the bench layer must still produce slugs that line up with gold-standard names.


def test_bench_run_canonical_name_normalizes_go():
    assert bench_run._canonical_name("github.com/gin-gonic/gin", "go") == "gin"
    assert bench_run._canonical_name("github.com/Owner/Repo", "go") == "repo"
    # Bare slug for Go should still pass through (already-normalized).
    assert bench_run._canonical_name("gin", "go") == "gin"


def test_bench_run_canonical_name_lowercases_python():
    # Non-go ecosystems must NOT slash-split — package names with slashes are
    # legitimate (e.g. npm scoped packages "@scope/pkg") and stripping them
    # would break matching.
    assert bench_run._canonical_name("Requests", "python") == "requests"
    assert bench_run._canonical_name("@scope/pkg", "javascript") == "@scope/pkg"
    assert bench_run._canonical_name("Tokio", "rust") == "tokio"


# preamble must trace a run back to a specific index/embed/git state.


def test_bench_run_preamble_includes_required_keys(tmp_path: Path, capsys):
    fixture = tmp_path / "tiny_gold.jsonl"
    fixture.write_text(
        json.dumps({"query": "http", "language": "python", "relevant_grades": {"requests": 2}})
        + "\n"
    )

    # Stub baselines so the test is fully offline (no HF Hub / no shard load).
    fake_baselines = {"semantic": lambda q, lang, k: ["requests"]}
    with (
        patch.object(bench_run, "BASELINES", fake_baselines),
        patch("sys.argv", ["bench.run", "--fixture", str(fixture), "--baselines", "semantic"]),
    ):
        bench_run.main()

    out = capsys.readouterr().out
    # Output is a single JSON envelope — find it by skipping any logging noise.
    payload = json.loads(out[out.index("{") :])
    assert "meta" in payload
    meta = payload["meta"]
    for key in (
        "timestamp_utc",
        "priorart_git_sha",
        "gold_standard_sha",
        "index_manifest_version",
        "embed_model_name",
        "fixture_path",
    ):
        assert key in meta, f"missing key {key} in meta preamble"
    # embed_model_name should resolve to the real bge-small constant.
    assert meta["embed_model_name"] == "BAAI/bge-small-en-v1.5"


# per-language stratification must surface ecosystem-specific regressions.


def test_bench_run_per_language_output(tmp_path: Path):
    gold = [
        {"query": "http", "language": "python", "relevant_grades": {"requests": 2}},
        {"query": "router", "language": "go", "relevant_grades": {"gin": 2}},
    ]
    fake_baselines = {
        # Always returns the right answer for python, wrong for go — so the
        # per-language slice is meaningful (python 1.0, go 0.0, overall 0.5).
        "semantic": lambda q, lang, k: ["requests"] if lang == "python" else ["wrong"],
    }
    with patch.object(bench_run, "BASELINES", fake_baselines):
        results = bench_run.evaluate_stratified(gold, k=10, baselines=["semantic"])

    assert set(results) == {"python", "go", "overall"}
    assert results["python"]["semantic"]["recall@10"] == 1.0
    assert results["go"]["semantic"]["recall@10"] == 0.0
    assert results["overall"]["semantic"]["recall@10"] == 0.5


def test_bench_run_per_language_main_output(tmp_path: Path, capsys):
    """End-to-end: main() emits a per-language results dict with overall last."""
    fixture = tmp_path / "multi_lang.jsonl"
    fixture.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "query": "http",
                        "language": "python",
                        "relevant_grades": {"requests": 2},
                    }
                ),
                json.dumps({"query": "router", "language": "go", "relevant_grades": {"gin": 2}}),
            ]
        )
        + "\n"
    )

    fake_baselines = {"semantic": lambda q, lang, k: ["requests", "gin"]}
    with (
        patch.object(bench_run, "BASELINES", fake_baselines),
        patch("sys.argv", ["bench.run", "--fixture", str(fixture), "--baselines", "semantic"]),
    ):
        bench_run.main()

    out = capsys.readouterr().out
    payload = json.loads(out[out.index("{") :])
    assert "results" in payload
    results = payload["results"]
    assert "python" in results
    assert "go" in results
    assert "overall" in results
    # Overall must be the last key — the printed JSON is what the user reads,
    # so insertion order matters for "overall last" UX.
    assert list(results.keys())[-1] == "overall"


def test_git_rev_parse_returns_unknown_on_failure():
    """`git rev-parse` over a missing path should fall back to "unknown" (not raise)."""
    assert bench_run._git_rev_parse("HEAD:no/such/file/anywhere.jsonl") == "unknown"


def test_index_manifest_version_returns_unknown_when_missing(monkeypatch, tmp_path):
    """If the cached manifest is absent, surface "unknown" rather than crashing the bench."""
    monkeypatch.setenv("PRIORART_INDEX_DIR", str(tmp_path / "empty"))
    # Reload the helper-resolved index_dir by calling fresh — the helper resolves
    # PRIORART_INDEX_DIR each call.
    assert bench_run._index_manifest_version() == "unknown"


def test_evaluate_stratified_orders_overall_last():
    """Stable output ordering: per-language sorted alphabetically, overall last."""
    gold = [
        {"query": "q1", "language": "rust", "relevant_grades": {"a": 2}},
        {"query": "q2", "language": "python", "relevant_grades": {"b": 2}},
        {"query": "q3", "language": "go", "relevant_grades": {"c": 2}},
    ]
    fake_baselines = {"semantic": lambda q, lang, k: ["a", "b", "c"]}
    with patch.object(bench_run, "BASELINES", fake_baselines):
        results = bench_run.evaluate_stratified(gold, k=10, baselines=["semantic"])

    keys = list(results.keys())
    assert keys == ["go", "python", "rust", "overall"]


@pytest.mark.parametrize(
    "name,language,expected",
    [
        ("github.com/sirupsen/logrus", "go", "logrus"),
        ("github.com/SIRUPSEN/Logrus", "go", "logrus"),
        ("logrus", "go", "logrus"),
        ("Pillow", "python", "pillow"),
        ("@types/node", "javascript", "@types/node"),
        ("serde", "rust", "serde"),
    ],
)
def test_canonical_name_table(name, language, expected):
    assert bench_run._canonical_name(name, language) == expected


# hand-curated validation fixture is the realistic-developer-query side
# of the benchmark — the awesome-list categories are a noisy proxy for what
# users actually search for. Existence + schema check is enough here; running
# the bench against it is documented in BENCHMARK.md.


def test_handpicked_fixture_loads_and_evaluates():
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "bench"
        / "fixtures"
        / "gold_standard_handpicked.jsonl"
    )
    rows = bench_run._load_gold(fixture_path)

    assert len(rows) == 8
    expected_pairs = {
        ("http client", "python"),
        ("json parser", "python"),
        ("jwt parser", "python"),
        ("http client", "javascript"),
        ("date manipulation", "javascript"),
        ("http client", "rust"),
        ("cli parser", "rust"),
        ("http router", "go"),
    }
    assert {(r["query"], r["language"]) for r in rows} == expected_pairs
    # All entries are hand-curated canonical answers — graded uniformly at 2.
    for row in rows:
        grades = row["relevant_grades"]
        assert grades, f"empty relevant_grades for {row['query']}"
        assert all(g == 2 for g in grades.values()), (
            f"non-grade-2 entry in {row['query']}: {grades}"
        )
