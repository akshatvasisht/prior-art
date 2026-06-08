"""
Client-side retrieval for the priorart hosted semantic package index.

Given a natural-language task and a language hint, this module:

1. Embeds the query with ``BAAI/bge-small-en-v1.5`` (fastembed, ONNX-only, no torch).
2. L2-normalizes and int8-quantizes the vector to match the shard's storage dtype.
3. Runs a usearch HNSW cosine query against the per-ecosystem shard.
4. Joins hits against the shard's ``metadata.jsonl`` sidecar.
5. Fuses dense hits with a BM25 ranking via RRF — the dense retriever's
   literal-matching bias means name-token matches like ``httplib2`` outrank
   semantically dominant packages like ``requests``; BM25 over the same
   metadata acts as a sparse safety net.
6. Falls back to a live registry search when the top dense match is below a
   similarity floor (index is stale or query is too novel).

The shard + manifest are fetched on first use via ``index_download``.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .hybrid import BM25Index, reciprocal_rank_fusion
from .index_download import ShardPaths, ensure_shard
from .registry import PackageCandidate, get_registry_client
from .utils import load_config

logger = logging.getLogger(__name__)

EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384
# Default cosine similarity floor below which we don't trust the index and fall
# back to live registry search. Conservative — bge-small self-similarity on
# semantically close pairs typically sits >0.65. Overridable via config.yaml
# `retrieval.similarity_floor`; this constant is the fallback.
SIMILARITY_FLOOR = 0.5


# Default convex weight on the popularity prior blended into the fused ranking
# (see hybrid.reciprocal_rank_fusion). Overridable per-ecosystem via config.yaml
# `retrieval.popularity_weight`. Dormant on shards built before the popularity
# field was added to metadata.jsonl — no data means an all-zero prior, which is
# order-preserving, so this is a safe no-op until the next index rebuild.
POPULARITY_WEIGHT = 0.3


def _per_ecosystem_setting(key: str, ecosystem: str, default: float) -> float:
    """Resolve a ``retrieval.<key>`` value that may be a single float or a
    per-ecosystem mapping with a ``default`` key. Falls back to ``default`` on
    any miss or error (including a non-numeric configured value)."""
    try:
        configured = load_config()["retrieval"].get(key, default)
        if isinstance(configured, dict):
            value = configured.get(ecosystem)
            if value is None:
                value = configured.get("default", default)
            return float(value)
        return float(configured)
    except Exception:
        return default


def _similarity_floor(ecosystem: str) -> float:
    """Resolve the similarity floor for ``ecosystem``. ``retrieval.similarity_floor``
    may be a single float or a per-ecosystem mapping; falls back to
    ``SIMILARITY_FLOOR``."""
    return _per_ecosystem_setting("similarity_floor", ecosystem, SIMILARITY_FLOOR)


def _popularity_weight(ecosystem: str) -> float:
    """Resolve the popularity-prior weight for ``ecosystem``.
    ``retrieval.popularity_weight`` may be a single float or a per-ecosystem
    mapping; falls back to ``POPULARITY_WEIGHT``."""
    return _per_ecosystem_setting("popularity_weight", ecosystem, POPULARITY_WEIGHT)


# how many candidates to ask each retriever (dense, BM25) for before RRF
# fusion. Larger pools deepen the fused tail without changing the head much
# unless one retriever has a sharply different ranking — 50 is a reasonable
# balance for an ecosystem of ~20K packages.
HYBRID_POOL = 50

# relative weights of dense vs. BM25 in RRF fusion. Dense weight is
# higher because semantic relevance is generally stronger; BM25 is the
# safety net for queries where dense fails on lexical bias.
DENSE_WEIGHT = 0.7
BM25_WEIGHT = 0.3

_LANGUAGE_TO_ECOSYSTEM = {
    "python": "python",
    "javascript": "npm",
    "typescript": "npm",
    "js": "npm",
    "ts": "npm",
    "node": "npm",
    "rust": "crates",
    "go": "go",
    "golang": "go",
    "java": "maven",
    "kotlin": "maven",
    "scala": "maven",
    "csharp": "nuget",
    "c#": "nuget",
    "dotnet": "nuget",
    "fsharp": "nuget",
}


@dataclass
class RetrievalHit:
    """A single retrieval result before registry enrichment."""

    name: str
    registry: str
    description: str
    github_url: str | None
    similarity: float


def _ecosystem_for(language: str) -> str:
    key = language.lower().strip()
    if key not in _LANGUAGE_TO_ECOSYSTEM:
        raise ValueError(
            f"Unsupported language '{language}'. Supported: {sorted(set(_LANGUAGE_TO_ECOSYSTEM))}"
        )
    return _LANGUAGE_TO_ECOSYSTEM[key]


@lru_cache(maxsize=1)
def _embedder():
    """Lazy-load the fastembed model once per process."""
    from fastembed import TextEmbedding  # type: ignore

    return TextEmbedding(model_name=EMBED_MODEL_NAME)


def _embed_query_int8(text: str):
    """Embed ``text`` and int8-quantize the result to match shard dtype."""
    import numpy as np

    model = _embedder()
    vec = next(iter(model.embed([text])))
    vec = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec = vec / norm
    # Unit vectors sit in [-1, 1]; scale to int8 range.
    return np.clip(np.round(vec * 127.0), -127, 127).astype(np.int8)


def _load_metadata(metadata_path: Path) -> dict[int, dict[str, Any]]:
    """Load the shard's jsonl sidecar into a key→record map."""
    records: dict[int, dict[str, Any]] = {}
    with metadata_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = rec.get("key")
            if isinstance(key, int):
                records[key] = rec
    return records


class Retriever:
    """Per-ecosystem semantic retriever backed by a usearch HNSW shard."""

    def __init__(self, ecosystem: str):
        self.ecosystem = ecosystem
        self._shard: ShardPaths | None = None
        self._index: Any = None  # usearch.index.Index (imported lazily in _ensure_loaded)
        self._metadata: dict[int, dict[str, Any]] | None = None
        # BM25 index built lazily over the same metadata sidecar so the
        # tokenization cost is paid once and amortized across queries.
        self._bm25: BM25Index | None = None
        # Name → metadata record, for hydrating BM25 hits (which return only
        # names) into RetrievalHit objects without re-scanning the sidecar.
        self._name_to_record: dict[str, dict[str, Any]] | None = None
        # Name → log-normalized popularity in [0, 1], computed once from the
        # shard's ``popularity`` field. ``None`` once computed means the shard
        # predates the field. The flag distinguishes "not yet computed" from
        # the legitimate ``None`` result.
        self._popularity: dict[str, float] | None = None
        self._popularity_computed = False

    def _ensure_loaded(self) -> None:
        if self._index is not None:
            return
        from usearch.index import Index  # type: ignore

        shard = ensure_shard(self.ecosystem)
        index = Index(ndim=EMBED_DIM, metric="cos", dtype="i8")
        index.load(str(shard.usearch_path))
        self._shard = shard
        self._index = index
        metadata = _load_metadata(shard.metadata_path)
        self._metadata = metadata
        # Index by name for O(1) hydration of BM25 results. If two records
        # share a name (rare; cross-registry collision in Go), the later
        # wins — fine because we filter by registry downstream anyway.
        self._name_to_record = {rec["name"]: rec for rec in metadata.values() if "name" in rec}

    def _ensure_bm25(self) -> BM25Index:
        """Build the BM25 index lazily on first use, then memoize on self."""
        self._ensure_loaded()
        if self._bm25 is None:
            assert self._metadata is not None
            names: list[str] = []
            descs: list[str] = []
            for rec in self._metadata.values():
                names.append(rec.get("name", ""))
                descs.append(rec.get("description", "") or "")
            self._bm25 = BM25Index(names, descs)
        return self._bm25

    def popularity_scores(self) -> dict[str, float] | None:
        """Per-name log-normalized popularity in ``[0, 1]``, memoized.

        Returns ``None`` when the shard carries no usable ``popularity`` field
        (shards built before the field was added) so callers can skip the
        prior entirely rather than blend an all-zero map.
        """
        if self._popularity_computed:
            return self._popularity
        self._ensure_loaded()
        assert self._metadata is not None
        raw: dict[str, float] = {}
        for rec in self._metadata.values():
            name = rec.get("name")
            pop = rec.get("popularity")
            if name and isinstance(pop, (int, float)) and not isinstance(pop, bool) and pop > 0:
                raw[name] = float(pop)
        if raw:
            denom = math.log1p(max(raw.values()))
            self._popularity = {name: math.log1p(pop) / denom for name, pop in raw.items()}
        else:
            self._popularity = None
        self._popularity_computed = True
        return self._popularity

    def search_dense(self, query: str, k: int = 20) -> list[RetrievalHit]:
        """Return up to ``k`` dense hits ranked by cosine similarity."""
        self._ensure_loaded()
        assert self._index is not None and self._metadata is not None

        qvec = _embed_query_int8(query)
        matches = self._index.search(qvec, count=k)

        hits: list[RetrievalHit] = []
        for m in matches:
            key = int(m.key)
            rec = self._metadata.get(key)
            if not rec:
                continue
            # usearch cosine distance ∈ [0, 2]; convert to similarity ∈ [-1, 1].
            similarity = 1.0 - float(m.distance)
            hits.append(
                RetrievalHit(
                    name=rec["name"],
                    registry=rec["registry"],
                    description=rec.get("description", "") or "",
                    github_url=rec.get("github_url"),
                    similarity=similarity,
                )
            )
        return hits

    # Back-compat alias for callers and tests that pre-date the dense/BM25 split.
    def search(self, query: str, k: int = 20) -> list[RetrievalHit]:
        return self.search_dense(query, k=k)

    def search_bm25(self, query: str, k: int = 50) -> list[str]:
        """Return up to ``k`` package names by BM25 score, descending."""
        bm25 = self._ensure_bm25()
        return bm25.search(query, k=k)

    def hydrate(self, name: str) -> RetrievalHit | None:
        """Look up a name in the metadata sidecar; return None if missing."""
        self._ensure_loaded()
        assert self._name_to_record is not None
        rec = self._name_to_record.get(name)
        if not rec:
            return None
        return RetrievalHit(
            name=rec["name"],
            registry=rec["registry"],
            description=rec.get("description", "") or "",
            github_url=rec.get("github_url"),
            # Similarity is unknown for BM25-only hits surfaced via fusion;
            # 0.0 is a placeholder that callers should not rely on.
            similarity=0.0,
        )


@lru_cache(maxsize=8)
def _retriever_for(ecosystem: str) -> Retriever:
    return Retriever(ecosystem)


def _hit_to_candidate(hit: RetrievalHit) -> PackageCandidate:
    # Go's registry stores full module paths ("github.com/gin-gonic/gin")
    # while every other ecosystem uses bare slugs ("requests", "axios").
    # Normalize at the candidate boundary so CLI/MCP/bench callers see a
    # consistent shape; the underlying metadata record is untouched.
    name = hit.name.split("/")[-1] if hit.registry == "go" else hit.name
    return PackageCandidate(
        name=name,
        registry=hit.registry,
        description=hit.description,
        github_url=hit.github_url,
    )


def retrieve_candidates(
    task_description: str,
    language: str,
    max_results: int = 20,
    lite: bool = False,
) -> list[PackageCandidate]:
    """Return ranked candidates for ``task_description`` in ``language``.

    Pipeline:
    1. Run dense retrieval (top ``HYBRID_POOL``).
    2. If dense top similarity < ``SIMILARITY_FLOOR``, the index can't answer
       confidently — fall back to live registry search.
    3. Otherwise, run BM25 over the same metadata sidecar (top ``HYBRID_POOL``)
       and fuse via RRF, weighting dense > BM25.

    When ``lite=True``, skip the semantic index entirely and use the registry
    search path directly (no shard download, no embedding model load).
    """
    # Validate language even in lite mode so errors stay consistent.
    ecosystem = _ecosystem_for(language)

    if lite:
        return _registry_fallback(task_description, language, max_results)

    try:
        retriever = _retriever_for(ecosystem)
        # max_results is the *output* size; we retrieve a deeper pool from
        # each retriever so RRF has enough material to re-rank meaningfully.
        pool = max(HYBRID_POOL, max_results)
        dense_hits = retriever.search_dense(task_description, k=pool)
    except Exception as e:
        logger.warning(f"Semantic index unavailable ({e}); falling back to registry search")
        return _registry_fallback(task_description, language, max_results)

    if not dense_hits or dense_hits[0].similarity < _similarity_floor(ecosystem):
        logger.info(
            "Top semantic match below floor "
            f"({dense_hits[0].similarity if dense_hits else 'n/a'}); "
            "falling back to registry search"
        )
        return _registry_fallback(task_description, language, max_results)

    # BM25 over the same corpus. Failures here are non-fatal: degrade to
    # dense-only rather than break the whole retrieval path.
    try:
        bm25_names = retriever.search_bm25(task_description, k=pool)
    except Exception as e:
        logger.warning(f"BM25 search failed ({e}); using dense-only ranking")
        bm25_names = []

    return _fuse_and_hydrate(retriever, dense_hits, bm25_names, max_results)


def _fuse_and_hydrate(
    retriever: Retriever,
    dense_hits: list[RetrievalHit],
    bm25_names: list[str],
    max_results: int,
) -> list[PackageCandidate]:
    """Combine dense + BM25 rankings via RRF, then hydrate to candidates.

    Hits keyed by name throughout — RRF fuses on name identity, then we
    reuse dense's pre-built RetrievalHit when available (carries similarity)
    or hydrate from the metadata sidecar otherwise.
    """
    if max_results <= 0:
        return []
    dense_by_name = {h.name: h for h in dense_hits}
    dense_ranking = [h.name for h in dense_hits]

    # Dense-only fast path: skip RRF when BM25 returned nothing useful.
    if not bm25_names:
        return [_hit_to_candidate(h) for h in dense_hits[:max_results]]

    # Blend a popularity prior into the fusion so that, among the packages the
    # two retrievers surface, the more-entrenched ones rank higher — closing the
    # gap where pure cosine/BM25 favors obscure name-matches over the popular
    # library a user actually wants. No-op on shards without the field.
    fused = reciprocal_rank_fusion(
        [dense_ranking, bm25_names],
        weights=[DENSE_WEIGHT, BM25_WEIGHT],
        priors=retriever.popularity_scores(),
        prior_weight=_popularity_weight(retriever.ecosystem),
    )

    # RRF deduplicates by construction (it's a dict-keyed sum), so iterating
    # ``fused`` yields each name exactly once. We just need to break early
    # once max_results is reached and skip names that hydration can't resolve.
    out: list[PackageCandidate] = []
    for name in fused:
        hit = dense_by_name.get(name) or retriever.hydrate(name)
        if hit is None:
            # BM25 returned a name that isn't in the metadata sidecar — should
            # be impossible since BM25 is built from the same map, but guard
            # rather than crash.
            continue
        out.append(_hit_to_candidate(hit))
        if len(out) >= max_results:
            break
    return out


def _registry_fallback(
    task_description: str, language: str, max_results: int
) -> list[PackageCandidate]:
    """Live registry search when the semantic index can't answer confidently."""
    try:
        with get_registry_client(language) as client:
            return client.search(task_description, max_results)
    except Exception as e:
        logger.error(f"Registry fallback failed: {e}")
        return []
