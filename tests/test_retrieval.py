"""Tests for core.retrieval — semantic shard search with registry fallback."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from priorart.core.retrieval import (
    RetrievalHit,
    Retriever,
    _ecosystem_for,
    _embed_query_int8,
    _embedder,
    _hit_to_candidate,
    _load_metadata,
    _registry_fallback,
    _retriever_for,
    retrieve_candidates,
)


def test_ecosystem_for_maps_common_languages():
    assert _ecosystem_for("python") == "python"
    assert _ecosystem_for("javascript") == "npm"
    assert _ecosystem_for("TypeScript") == "npm"
    assert _ecosystem_for("rust") == "crates"
    assert _ecosystem_for("go") == "go"
    assert _ecosystem_for("golang") == "go"


def test_ecosystem_for_rejects_unknown():
    with pytest.raises(ValueError, match="Unsupported language"):
        _ecosystem_for("cobol")


def test_hit_to_candidate_preserves_fields():
    hit = RetrievalHit(
        name="requests",
        registry="pypi",
        description="HTTP for Humans",
        github_url="https://github.com/psf/requests",
        similarity=0.82,
    )
    cand = _hit_to_candidate(hit)
    assert cand.name == "requests"
    assert cand.registry == "pypi"
    assert cand.github_url == "https://github.com/psf/requests"


@patch("priorart.core.retrieval._retriever_for")
def test_retrieve_candidates_returns_semantic_hits_above_floor(mock_get_retriever):
    retriever = MagicMock()
    retriever.search.return_value = [
        RetrievalHit("requests", "pypi", "HTTP", "https://github.com/psf/requests", 0.82),
        RetrievalHit("httpx", "pypi", "HTTP", "https://github.com/encode/httpx", 0.74),
    ]
    mock_get_retriever.return_value = retriever

    results = retrieve_candidates("http client", "python", max_results=10)

    assert len(results) == 2
    assert results[0].name == "requests"
    retriever.search.assert_called_once_with("http client", k=10)


@patch("priorart.core.retrieval._registry_fallback")
@patch("priorart.core.retrieval._retriever_for")
def test_retrieve_candidates_falls_back_when_below_floor(mock_get_retriever, mock_fallback):
    retriever = MagicMock()
    retriever.search.return_value = [
        RetrievalHit("random", "pypi", "", None, 0.21),
    ]
    mock_get_retriever.return_value = retriever
    mock_fallback.return_value = ["fallback-candidate"]

    results = retrieve_candidates("obscure query", "python")

    assert results == ["fallback-candidate"]
    mock_fallback.assert_called_once()


@patch("priorart.core.retrieval._registry_fallback")
@patch("priorart.core.retrieval._retriever_for")
def test_retrieve_candidates_falls_back_when_index_errors(mock_get_retriever, mock_fallback):
    retriever = MagicMock()
    retriever.search.side_effect = RuntimeError("shard missing")
    mock_get_retriever.return_value = retriever
    mock_fallback.return_value = []

    results = retrieve_candidates("http client", "python")

    assert results == []
    mock_fallback.assert_called_once()


@patch("priorart.core.retrieval._registry_fallback")
@patch("priorart.core.retrieval._retriever_for")
def test_retrieve_candidates_falls_back_when_no_hits(mock_get_retriever, mock_fallback):
    retriever = MagicMock()
    retriever.search.return_value = []
    mock_get_retriever.return_value = retriever
    mock_fallback.return_value = []

    results = retrieve_candidates("anything", "python")

    assert results == []
    mock_fallback.assert_called_once()


@patch("priorart.core.retrieval._registry_fallback")
@patch("priorart.core.retrieval._retriever_for")
def test_retrieve_candidates_lite_mode_skips_semantic_path(mock_get_retriever, mock_fallback):
    """lite=True should go straight to the registry fallback without touching the index."""
    mock_fallback.return_value = ["lite-candidate"]

    results = retrieve_candidates("http client", "python", lite=True)

    assert results == ["lite-candidate"]
    mock_fallback.assert_called_once()
    mock_get_retriever.assert_not_called()


def test_retrieve_candidates_lite_still_validates_language():
    with pytest.raises(ValueError, match="Unsupported language"):
        retrieve_candidates("anything", "cobol", lite=True)


def test_embed_query_int8_returns_int8_vector():
    _embedder.cache_clear()
    fake_vec = np.linspace(0.0, 0.5, 384, dtype=np.float32)
    fake_model = MagicMock()
    fake_model.embed.return_value = iter([fake_vec])
    with patch("priorart.core.retrieval._embedder", return_value=fake_model):
        out = _embed_query_int8("hello")
    assert out.dtype == np.int8
    assert out.shape == (384,)
    assert out.min() >= -127
    assert out.max() <= 127


def test_embed_query_int8_handles_zero_vector():
    _embedder.cache_clear()
    fake_vec = np.zeros(384, dtype=np.float32)
    fake_model = MagicMock()
    fake_model.embed.return_value = iter([fake_vec])
    with patch("priorart.core.retrieval._embedder", return_value=fake_model):
        out = _embed_query_int8("hello")
    assert out.dtype == np.int8
    assert out.shape == (384,)
    assert np.all(out == 0)


def test_load_metadata_skips_blank_and_bad_json(tmp_path):
    path = tmp_path / "metadata.jsonl"
    path.write_text(
        "\n"
        '{"key": 1, "name": "a"}\n'
        "   \n"
        "{not valid json\n"
        '{"name": "missing-key"}\n'
        '{"key": "not-int", "name": "b"}\n',
        encoding="utf-8",
    )
    result = _load_metadata(path)
    assert list(result.keys()) == [1]
    assert result[1]["name"] == "a"


def _make_match(key, distance):
    m = MagicMock()
    m.key = key
    m.distance = distance
    return m


@patch("priorart.core.retrieval._embed_query_int8")
@patch("priorart.core.retrieval._load_metadata")
@patch("priorart.core.retrieval.ensure_shard")
def test_retriever_search_returns_hits(mock_ensure, mock_load_meta, mock_embed):
    shard = MagicMock()
    shard.usearch_path = "/tmp/fake.usearch"
    shard.metadata_path = "/tmp/fake.jsonl"
    mock_ensure.return_value = shard
    mock_load_meta.return_value = {
        0: {
            "name": "requests",
            "registry": "pypi",
            "description": "HTTP",
            "github_url": "https://github.com/psf/requests",
        },
        1: {
            "name": "httpx",
            "registry": "pypi",
            "description": "HTTP",
            "github_url": None,
        },
    }
    mock_embed.return_value = np.zeros(384, dtype=np.int8)

    fake_index = MagicMock()
    fake_index.search.return_value = [_make_match(0, 0.2), _make_match(1, 0.4)]

    fake_usearch_index = MagicMock()
    fake_usearch_index.Index = MagicMock(return_value=fake_index)
    fake_usearch = MagicMock()
    fake_usearch.index = fake_usearch_index
    with (
        patch.dict("sys.modules", {"usearch": fake_usearch, "usearch.index": fake_usearch_index}),
        patch("usearch.index.Index", fake_usearch_index.Index) as mock_index_cls,
    ):
        r = Retriever("python")
        hits = r.search("http", k=5)

    assert len(hits) == 2
    assert hits[0].name == "requests"
    assert hits[0].similarity == pytest.approx(0.8)
    assert hits[1].name == "httpx"
    assert hits[1].similarity == pytest.approx(0.6)
    mock_index_cls.assert_called_once()


@patch("priorart.core.retrieval._embed_query_int8")
@patch("priorart.core.retrieval._load_metadata")
@patch("priorart.core.retrieval.ensure_shard")
def test_retriever_search_skips_missing_metadata_keys(mock_ensure, mock_load_meta, mock_embed):
    shard = MagicMock()
    shard.usearch_path = "/tmp/fake.usearch"
    shard.metadata_path = "/tmp/fake.jsonl"
    mock_ensure.return_value = shard
    mock_load_meta.return_value = {
        0: {"name": "requests", "registry": "pypi", "description": "", "github_url": None},
    }
    mock_embed.return_value = np.zeros(384, dtype=np.int8)

    fake_index = MagicMock()
    fake_index.search.return_value = [_make_match(0, 0.1), _make_match(999, 0.3)]

    fake_usearch_index = MagicMock()
    fake_usearch_index.Index = MagicMock(return_value=fake_index)
    fake_usearch = MagicMock()
    fake_usearch.index = fake_usearch_index
    with patch.dict("sys.modules", {"usearch": fake_usearch, "usearch.index": fake_usearch_index}):
        hits = Retriever("python").search("http", k=5)

    assert len(hits) == 1
    assert hits[0].name == "requests"


@patch("priorart.core.retrieval._embed_query_int8")
@patch("priorart.core.retrieval._load_metadata")
@patch("priorart.core.retrieval.ensure_shard")
def test_retriever_ensure_loaded_is_idempotent(mock_ensure, mock_load_meta, mock_embed):
    shard = MagicMock()
    shard.usearch_path = "/tmp/fake.usearch"
    shard.metadata_path = "/tmp/fake.jsonl"
    mock_ensure.return_value = shard
    mock_load_meta.return_value = {}
    mock_embed.return_value = np.zeros(384, dtype=np.int8)

    fake_index = MagicMock()
    fake_index.search.return_value = []

    fake_usearch_index = MagicMock()
    fake_usearch_index.Index = MagicMock(return_value=fake_index)
    fake_usearch = MagicMock()
    fake_usearch.index = fake_usearch_index
    with (
        patch.dict("sys.modules", {"usearch": fake_usearch, "usearch.index": fake_usearch_index}),
        patch("usearch.index.Index", fake_usearch_index.Index) as mock_index_cls,
    ):
        r = Retriever("python")
        r.search("a")
        r.search("b")

    assert mock_ensure.call_count == 1
    assert fake_index.load.call_count == 1
    assert mock_index_cls.call_count == 1


def test_retriever_for_caches_per_ecosystem():
    _retriever_for.cache_clear()
    r1 = _retriever_for("python")
    r2 = _retriever_for("python")
    r3 = _retriever_for("npm")
    assert r1 is r2
    assert r1 is not r3


@patch("priorart.core.retrieval.get_registry_client")
def test_registry_fallback_success(mock_get_client):
    from priorart.core.registry import PackageCandidate

    cand = PackageCandidate(name="requests", registry="pypi", description="HTTP", github_url=None)
    client = MagicMock()
    client.search.return_value = [cand]
    mock_get_client.return_value.__enter__.return_value = client

    result = _registry_fallback("query", "python", 10)

    assert result == [cand]
    client.search.assert_called_once_with("query", 10)


@patch("priorart.core.retrieval.get_registry_client")
def test_registry_fallback_exception_returns_empty(mock_get_client):
    mock_get_client.side_effect = RuntimeError("boom")
    assert _registry_fallback("query", "python", 10) == []


def test_embedder_is_lru_cached():
    _embedder.cache_clear()
    fake_cls = MagicMock()
    fake_module = MagicMock()
    fake_module.TextEmbedding = fake_cls
    with patch.dict("sys.modules", {"fastembed": fake_module}):
        _embedder()
        _embedder()
    assert fake_cls.call_count == 1
    _embedder.cache_clear()
