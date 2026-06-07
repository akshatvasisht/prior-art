"""Tests for core.retrieval — semantic shard search with registry fallback."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from priorart.core import retrieval
from priorart.core.retrieval import (
    SIMILARITY_FLOOR,
    RetrievalHit,
    Retriever,
    _ecosystem_for,
    _embed_query_int8,
    _embedder,
    _fuse_and_hydrate,
    _hit_to_candidate,
    _load_metadata,
    _registry_fallback,
    _retriever_for,
    _similarity_floor,
    retrieve_candidates,
)


def test_similarity_floor_reads_config():
    assert _similarity_floor("python") == 0.5  # single float in config.yaml


def test_similarity_floor_falls_back_when_key_missing(monkeypatch):
    monkeypatch.setattr(retrieval, "load_config", lambda: {"retrieval": {}})
    assert _similarity_floor("python") == SIMILARITY_FLOOR


def test_similarity_floor_falls_back_on_config_error(monkeypatch):
    def _boom():
        raise RuntimeError("no config")

    monkeypatch.setattr(retrieval, "load_config", _boom)
    assert _similarity_floor("python") == SIMILARITY_FLOOR


def test_similarity_floor_per_ecosystem_mapping(monkeypatch):
    monkeypatch.setattr(
        retrieval,
        "load_config",
        lambda: {"retrieval": {"similarity_floor": {"default": 0.55, "npm": 0.45}}},
    )
    assert _similarity_floor("npm") == 0.45  # per-ecosystem override
    assert _similarity_floor("python") == 0.55  # falls to the mapping's default

    # Mapping without a default: an unlisted ecosystem uses the in-code fallback.
    monkeypatch.setattr(
        retrieval, "load_config", lambda: {"retrieval": {"similarity_floor": {"npm": 0.45}}}
    )
    assert _similarity_floor("go") == SIMILARITY_FLOOR


def test_fuse_and_hydrate_returns_empty_for_nonpositive_max_results():
    """max_results <= 0 yields no candidates — the append-before-check loop
    previously returned one item for max_results=0."""
    assert _fuse_and_hydrate(MagicMock(), [], ["x"], 0) == []
    assert _fuse_and_hydrate(MagicMock(), [], ["x"], -1) == []


def test_ecosystem_for_maps_common_languages():
    assert _ecosystem_for("python") == "python"
    assert _ecosystem_for("javascript") == "npm"
    assert _ecosystem_for("TypeScript") == "npm"
    assert _ecosystem_for("rust") == "crates"
    assert _ecosystem_for("go") == "go"
    assert _ecosystem_for("golang") == "go"


def test_ecosystem_for_maps_jvm_and_dotnet():
    assert _ecosystem_for("java") == "maven"
    assert _ecosystem_for("kotlin") == "maven"
    assert _ecosystem_for("scala") == "maven"
    assert _ecosystem_for("csharp") == "nuget"
    assert _ecosystem_for("dotnet") == "nuget"
    assert _ecosystem_for("fsharp") == "nuget"


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


def test_hit_to_candidate_normalizes_go_module_paths():
    hit = RetrievalHit(
        name="github.com/gin-gonic/gin",
        registry="go",
        description="Web framework",
        github_url="https://github.com/gin-gonic/gin",
        similarity=0.71,
    )
    cand = _hit_to_candidate(hit)
    assert cand.name == "gin"
    assert cand.registry == "go"


def test_hit_to_candidate_preserves_python_names():
    hit = RetrievalHit(
        name="requests",
        registry="pypi",
        description="HTTP for Humans",
        github_url=None,
        similarity=0.82,
    )
    cand = _hit_to_candidate(hit)
    assert cand.name == "requests"


def test_hit_to_candidate_handles_go_with_subpath():
    # Documents the chosen behavior: deep subpaths collapse to the final
    # segment. Imperfect for modules like cosmos-sdk/x/bank where the user
    # might want "cosmos-sdk", but consistent and predictable.
    hit = RetrievalHit(
        name="github.com/cosmos/cosmos-sdk/x/bank",
        registry="go",
        description="",
        github_url=None,
        similarity=0.6,
    )
    cand = _hit_to_candidate(hit)
    assert cand.name == "bank"


@patch("priorart.core.retrieval._retriever_for")
def test_retrieve_candidates_returns_semantic_hits_above_floor(mock_get_retriever):
    retriever = MagicMock()
    retriever.search_dense.return_value = [
        RetrievalHit("requests", "pypi", "HTTP", "https://github.com/psf/requests", 0.82),
        RetrievalHit("httpx", "pypi", "HTTP", "https://github.com/encode/httpx", 0.74),
    ]
    # BM25 returns no overlap → dense ordering survives intact via RRF.
    retriever.search_bm25.return_value = []
    mock_get_retriever.return_value = retriever

    results = retrieve_candidates("http client", "python", max_results=10)

    assert len(results) == 2
    assert results[0].name == "requests"
    # max_results=10 < HYBRID_POOL=50, so the actual k passed is HYBRID_POOL.
    retriever.search_dense.assert_called_once_with("http client", k=50)


@patch("priorart.core.retrieval._registry_fallback")
@patch("priorart.core.retrieval._retriever_for")
def test_retrieve_candidates_falls_back_when_below_floor(mock_get_retriever, mock_fallback):
    retriever = MagicMock()
    retriever.search_dense.return_value = [
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
    retriever.search_dense.side_effect = RuntimeError("shard missing")
    mock_get_retriever.return_value = retriever
    mock_fallback.return_value = []

    results = retrieve_candidates("http client", "python")

    assert results == []
    mock_fallback.assert_called_once()


@patch("priorart.core.retrieval._registry_fallback")
@patch("priorart.core.retrieval._retriever_for")
def test_retrieve_candidates_falls_back_when_no_hits(mock_get_retriever, mock_fallback):
    retriever = MagicMock()
    retriever.search_dense.return_value = []
    mock_get_retriever.return_value = retriever
    mock_fallback.return_value = []

    results = retrieve_candidates("anything", "python")

    assert results == []
    mock_fallback.assert_called_once()


@patch("priorart.core.retrieval._retriever_for")
def test_retrieve_candidates_uses_hybrid_path(mock_get_retriever):
    """BM25 surfaces a doc that dense ranks deep, demonstrating fusion
    surfaces dual-signal matches above single-signal ones.

    With weights [0.7, 0.3] and k=60, a doc only present at dense rank 5+
    and BM25 rank 1 will overtake a doc that appears only in dense at rank
    1 — the BM25 contribution closes the gap once dense's lead is small.
    """
    retriever = MagicMock()
    # 'httplib2' wins dense (literal bias); 'requests' is buried at #5.
    retriever.search_dense.return_value = [
        RetrievalHit("httplib2", "pypi", "HTTP/2", None, 0.82),
        RetrievalHit("httpie", "pypi", "CLI HTTP", None, 0.78),
        RetrievalHit("httpx", "pypi", "Next-gen HTTP", None, 0.77),
        RetrievalHit("vonage-http-client", "pypi", "Vonage HTTP", None, 0.76),
        RetrievalHit("requests", "pypi", "HTTP for Humans", None, 0.74),
    ]
    # BM25 ranks 'requests' first; httplib2 doesn't appear in BM25 top-K.
    retriever.search_bm25.return_value = ["requests", "httpx", "httpie"]
    retriever.hydrate.return_value = None  # everything BM25 returns is in dense
    mock_get_retriever.return_value = retriever

    results = retrieve_candidates("http clients", "python", max_results=5)

    names = [c.name for c in results]
    # 'requests' must be in the top-5 thanks to BM25. Pre-hybrid, it was
    # behind three name-matchers and would routinely fall outside top-K.
    assert "requests" in names
    # And it must outrank vonage-http-client (a dense-only literal matcher).
    assert names.index("requests") < names.index("vonage-http-client")


@patch("priorart.core.retrieval._retriever_for")
def test_retrieve_candidates_falls_back_when_bm25_empty(mock_get_retriever):
    """Empty BM25 result → dense-only fast path (no RRF call)."""
    retriever = MagicMock()
    retriever.search_dense.return_value = [
        RetrievalHit("requests", "pypi", "HTTP", None, 0.82),
        RetrievalHit("httpx", "pypi", "HTTP", None, 0.74),
    ]
    retriever.search_bm25.return_value = []
    mock_get_retriever.return_value = retriever

    results = retrieve_candidates("http client", "python", max_results=10)

    # Dense-only ordering preserved.
    assert [c.name for c in results] == ["requests", "httpx"]


@patch("priorart.core.retrieval._retriever_for")
def test_retrieve_candidates_handles_bm25_exception(mock_get_retriever):
    """BM25 failure must degrade to dense-only, not crash the pipeline."""
    retriever = MagicMock()
    retriever.search_dense.return_value = [
        RetrievalHit("requests", "pypi", "HTTP", None, 0.82),
    ]
    retriever.search_bm25.side_effect = RuntimeError("bm25 corpus broken")
    mock_get_retriever.return_value = retriever

    results = retrieve_candidates("http client", "python", max_results=10)

    assert [c.name for c in results] == ["requests"]


@patch("priorart.core.retrieval._retriever_for")
def test_retrieve_candidates_hydrates_bm25_only_hit(mock_get_retriever):
    """A name BM25 surfaces that's NOT in the dense top-K still surfaces if
    fusion ranks it high enough — provided hydrate() can find it."""
    retriever = MagicMock()
    retriever.search_dense.return_value = [
        RetrievalHit("httplib2", "pypi", "HTTP/2", None, 0.82),
    ]
    retriever.search_bm25.return_value = ["requests", "httplib2"]
    # 'requests' is BM25-only; hydrate must produce a record for it.
    retriever.hydrate.return_value = RetrievalHit("requests", "pypi", "HTTP for Humans", None, 0.0)
    mock_get_retriever.return_value = retriever

    results = retrieve_candidates("http clients", "python", max_results=5)

    names = [c.name for c in results]
    assert "requests" in names
    assert "httplib2" in names


@patch("priorart.core.retrieval._retriever_for")
def test_retrieve_candidates_skips_unhydrated_bm25_hit(mock_get_retriever):
    """If hydrate returns None, that name is dropped from the fused list."""
    retriever = MagicMock()
    retriever.search_dense.return_value = [
        RetrievalHit("httpx", "pypi", "HTTP", None, 0.82),
    ]
    retriever.search_bm25.return_value = ["ghost-pkg", "httpx"]
    retriever.hydrate.return_value = None
    mock_get_retriever.return_value = retriever

    results = retrieve_candidates("http clients", "python", max_results=5)

    assert [c.name for c in results] == ["httpx"]


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


@patch("priorart.core.retrieval._embed_query_int8")
@patch("priorart.core.retrieval._load_metadata")
@patch("priorart.core.retrieval.ensure_shard")
def test_retriever_search_bm25_returns_names(mock_ensure, mock_load_meta, mock_embed):
    """search_bm25 builds the BM25 index lazily then returns name matches."""
    shard = MagicMock()
    shard.usearch_path = "/tmp/fake.usearch"
    shard.metadata_path = "/tmp/fake.jsonl"
    mock_ensure.return_value = shard
    mock_load_meta.return_value = {
        0: {"name": "requests", "registry": "pypi", "description": "HTTP for Humans"},
        1: {"name": "django", "registry": "pypi", "description": "Web framework"},
        2: {"name": "boto3", "registry": "pypi", "description": "AWS SDK"},
    }
    mock_embed.return_value = np.zeros(384, dtype=np.int8)
    fake_index = MagicMock()
    fake_usearch_index = MagicMock()
    fake_usearch_index.Index = MagicMock(return_value=fake_index)
    fake_usearch = MagicMock()
    fake_usearch.index = fake_usearch_index
    with patch.dict("sys.modules", {"usearch": fake_usearch, "usearch.index": fake_usearch_index}):
        r = Retriever("python")
        results = r.search_bm25("requests", k=5)

    assert "requests" in results


@patch("priorart.core.retrieval._embed_query_int8")
@patch("priorart.core.retrieval._load_metadata")
@patch("priorart.core.retrieval.ensure_shard")
def test_retriever_ensure_bm25_caches(mock_ensure, mock_load_meta, mock_embed):
    """_ensure_bm25 builds the BM25Index once and reuses it on subsequent calls."""
    shard = MagicMock()
    shard.usearch_path = "/tmp/fake.usearch"
    shard.metadata_path = "/tmp/fake.jsonl"
    mock_ensure.return_value = shard
    mock_load_meta.return_value = {
        0: {"name": "requests", "registry": "pypi", "description": "HTTP"},
    }
    mock_embed.return_value = np.zeros(384, dtype=np.int8)
    fake_index = MagicMock()
    fake_usearch_index = MagicMock()
    fake_usearch_index.Index = MagicMock(return_value=fake_index)
    fake_usearch = MagicMock()
    fake_usearch.index = fake_usearch_index
    with patch.dict("sys.modules", {"usearch": fake_usearch, "usearch.index": fake_usearch_index}):
        r = Retriever("python")
        b1 = r._ensure_bm25()
        b2 = r._ensure_bm25()

    assert b1 is b2


@patch("priorart.core.retrieval._embed_query_int8")
@patch("priorart.core.retrieval._load_metadata")
@patch("priorart.core.retrieval.ensure_shard")
def test_retriever_hydrate_returns_hit_for_known_name(mock_ensure, mock_load_meta, mock_embed):
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
    }
    mock_embed.return_value = np.zeros(384, dtype=np.int8)
    fake_index = MagicMock()
    fake_usearch_index = MagicMock()
    fake_usearch_index.Index = MagicMock(return_value=fake_index)
    fake_usearch = MagicMock()
    fake_usearch.index = fake_usearch_index
    with patch.dict("sys.modules", {"usearch": fake_usearch, "usearch.index": fake_usearch_index}):
        r = Retriever("python")
        hit = r.hydrate("requests")

    assert hit is not None
    assert hit.name == "requests"
    assert hit.registry == "pypi"
    # similarity is 0.0 placeholder for BM25-only hits.
    assert hit.similarity == 0.0


@patch("priorart.core.retrieval._embed_query_int8")
@patch("priorart.core.retrieval._load_metadata")
@patch("priorart.core.retrieval.ensure_shard")
def test_retriever_hydrate_returns_none_for_missing_name(mock_ensure, mock_load_meta, mock_embed):
    shard = MagicMock()
    shard.usearch_path = "/tmp/fake.usearch"
    shard.metadata_path = "/tmp/fake.jsonl"
    mock_ensure.return_value = shard
    mock_load_meta.return_value = {0: {"name": "requests", "registry": "pypi"}}
    mock_embed.return_value = np.zeros(384, dtype=np.int8)
    fake_index = MagicMock()
    fake_usearch_index = MagicMock()
    fake_usearch_index.Index = MagicMock(return_value=fake_index)
    fake_usearch = MagicMock()
    fake_usearch.index = fake_usearch_index
    with patch.dict("sys.modules", {"usearch": fake_usearch, "usearch.index": fake_usearch_index}):
        r = Retriever("python")
        assert r.hydrate("nonexistent-pkg") is None


@patch("priorart.core.retrieval._retriever_for")
def test_retrieve_candidates_returns_requests_for_http_clients_python(mock_get_retriever):
    """Regression: the bench run before hybrid showed `requests`, the
    most-popular Python HTTP library by orders of magnitude, did NOT appear
    in top-10 for query 'http clients' — dense alone preferred name-keyword
    matchers like httplib2, httpie, vonage-http-client.

    This test reconstructs the failure scenario: a dense ranking dominated
    by literal name-match noise with `requests` buried, plus a BM25 ranking
    that surfaces `requests` via description tokens. After RRF fusion,
    `requests` must be in the top-10 (the regression bar).
    """
    retriever = MagicMock()
    # Reproduces the observed (pre-hybrid) dense top-10 for 'http clients':
    # name-keyword matchers dominate, `requests` is far down or missing.
    retriever.search_dense.return_value = [
        RetrievalHit("httplib2", "pypi", "comprehensive HTTP client", None, 0.78),
        RetrievalHit("httpie", "pypi", "command-line HTTP client", None, 0.77),
        RetrievalHit("httpx", "pypi", "next generation HTTP client", None, 0.76),
        RetrievalHit("vonage-http-client", "pypi", "vonage http client", None, 0.74),
        RetrievalHit("aiohttp", "pypi", "async HTTP client/server", None, 0.73),
        RetrievalHit("urllib3", "pypi", "HTTP library", None, 0.72),
        RetrievalHit("h2", "pypi", "HTTP/2 protocol stack", None, 0.71),
        RetrievalHit("httplib", "pypi", "HTTP module", None, 0.70),
        RetrievalHit("simple-http-client", "pypi", "tiny HTTP client", None, 0.69),
        RetrievalHit("twisted-http", "pypi", "twisted HTTP", None, 0.68),
        RetrievalHit("requests", "pypi", "Python HTTP for Humans.", None, 0.67),
    ]
    # BM25 over the description corpus: 'requests' has the most informative
    # description for 'http' and beats single-token name-matchers.
    retriever.search_bm25.return_value = [
        "requests",
        "aiohttp",
        "httpx",
        "httplib2",
        "urllib3",
        "httpie",
    ]
    retriever.hydrate.return_value = None
    mock_get_retriever.return_value = retriever

    results = retrieve_candidates("http clients", "python", max_results=10)

    names = [c.name for c in results]
    assert "requests" in names, (
        f"requests must be in top-10 after hybrid fusion; got {names}. "
        "If this regression fires, the hybrid retrieval needs further tuning (BM25 weight, "
        "RRF k, name-token weighting)."
    )


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
