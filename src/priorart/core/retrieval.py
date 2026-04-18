"""
Client-side retrieval for the priorart hosted semantic package index.

Given a natural-language task and a language hint, this module:

1. Embeds the query with ``BAAI/bge-small-en-v1.5`` (fastembed, ONNX-only, no torch).
2. L2-normalizes and int8-quantizes the vector to match the shard's storage dtype.
3. Runs a usearch HNSW cosine query against the per-ecosystem shard.
4. Joins hits against the shard's ``metadata.jsonl`` sidecar.
5. Falls back to a live registry search when the top match is below a
   similarity floor (index is stale or query is too novel).

The shard + manifest are fetched on first use via ``index_download``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .index_download import ShardPaths, ensure_shard
from .registry import PackageCandidate, get_registry_client

logger = logging.getLogger(__name__)

EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384
# Cosine similarity floor below which we don't trust the index and
# fall back to live registry search. Chosen conservatively — bge-small
# self-similarity on semantically close pairs typically sits >0.65.
SIMILARITY_FLOOR = 0.5

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
        self._index = None
        self._metadata: dict[int, dict[str, Any]] | None = None

    def _ensure_loaded(self) -> None:
        if self._index is not None:
            return
        from usearch.index import Index  # type: ignore

        shard = ensure_shard(self.ecosystem)
        index = Index(ndim=EMBED_DIM, metric="cos", dtype="i8")
        index.load(str(shard.usearch_path))
        self._shard = shard
        self._index = index
        self._metadata = _load_metadata(shard.metadata_path)

    def search(self, query: str, k: int = 20) -> list[RetrievalHit]:
        """Return up to ``k`` hits ranked by cosine similarity."""
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


@lru_cache(maxsize=8)
def _retriever_for(ecosystem: str) -> Retriever:
    return Retriever(ecosystem)


def _hit_to_candidate(hit: RetrievalHit) -> PackageCandidate:
    return PackageCandidate(
        name=hit.name,
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

    Falls back to live registry search when the index's top similarity is
    below ``SIMILARITY_FLOOR`` or the index is unavailable. When ``lite``
    is True, skip the semantic index entirely (no shard download, no
    embedding model load) and use the registry search path directly.
    """
    # Validate language even in lite mode so errors stay consistent.
    _ecosystem_for(language)

    if lite:
        return _registry_fallback(task_description, language, max_results)

    try:
        retriever = _retriever_for(_ecosystem_for(language))
        hits = retriever.search(task_description, k=max_results)
    except Exception as e:
        logger.warning(f"Semantic index unavailable ({e}); falling back to registry search")
        return _registry_fallback(task_description, language, max_results)

    if not hits or hits[0].similarity < SIMILARITY_FLOOR:
        logger.info(
            "Top semantic match below floor "
            f"({hits[0].similarity if hits else 'n/a'}); falling back to registry search"
        )
        return _registry_fallback(task_description, language, max_results)

    return [_hit_to_candidate(h) for h in hits]


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
