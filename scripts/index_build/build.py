"""
Build per-ecosystem usearch shards + manifest for the priorart semantic index.

Pipeline:

1. Fetch package records per ecosystem (``fetch.fetch_ecosystem``).
2. Embed ``"{name}: {description}"`` with BAAI/bge-small-en-v1.5 (fastembed).
3. L2-normalize and int8-quantize (scale 127).
4. Build a usearch HNSW index (``dtype="i8"``, cosine), save.
5. Write ``metadata.jsonl`` sidecar with `{key, name, registry, description, github_url}`.
6. SHA-256 each shard file, write ``manifest.json``.

Sigstore signing of the manifest happens in CI (``sigstore sign``), not here —
this script is safe to run locally to smoke-test outputs.

Invocation::

    python -m scripts.index_build.build --out dist/index --ecosystems python,npm --top-n 5000
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .fetch import fetch_ecosystem

logger = logging.getLogger(__name__)

EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _quantize_int8(vectors):
    """Unit-normalize float32 rows then scale by 127 → int8."""
    import numpy as np

    arr = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    arr = arr / norms
    return np.clip(np.round(arr * 127.0), -127, 127).astype(np.int8)


def build_shard(
    ecosystem: str,
    out_dir: Path,
    manifest_date: str,
    top_n: int,
    batch_size: int = 256,
) -> dict:
    """Build one shard. Returns the manifest entry for it."""
    from fastembed import TextEmbedding  # type: ignore
    from usearch.index import Index  # type: ignore

    logger.info(f"Building shard for {ecosystem}")
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = f"{ecosystem}-{manifest_date}"
    usearch_path = out_dir / f"{stem}.usearch"
    metadata_path = out_dir / f"{stem}.metadata.jsonl"

    model = TextEmbedding(model_name=EMBED_MODEL_NAME)
    index = Index(ndim=EMBED_DIM, metric="cos", dtype="i8")

    seen: set[tuple[str, str]] = set()
    records: list[dict] = []
    for rec in fetch_ecosystem(ecosystem, top_n=top_n):
        key = (rec["name"], rec["registry"])
        if key in seen:
            continue
        seen.add(key)
        records.append(rec)

    logger.info(f"{ecosystem}: {len(records)} unique records to embed")

    with metadata_path.open("w", encoding="utf-8") as meta_f:
        for batch_start in range(0, len(records), batch_size):
            batch = records[batch_start : batch_start + batch_size]
            texts = [f"{r['name']}: {r['description']}" for r in batch]
            vectors = list(model.embed(texts))
            int8_vectors = _quantize_int8(vectors)

            for offset, rec in enumerate(batch):
                key = batch_start + offset
                index.add(key, int8_vectors[offset])
                meta_f.write(
                    json.dumps(
                        {
                            "key": key,
                            "name": rec["name"],
                            "registry": rec["registry"],
                            "description": rec["description"],
                            "github_url": rec.get("github_url"),
                        }
                    )
                    + "\n"
                )

    index.save(str(usearch_path))

    return {
        "usearch": usearch_path.name,
        "metadata": metadata_path.name,
        "usearch_sha256": _sha256(usearch_path),
        "metadata_sha256": _sha256(metadata_path),
        "record_count": len(records),
    }


def build_all(out_dir: Path, ecosystems: list[str], top_n: int) -> Path:
    """Build all shards + write manifest.json."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_date = datetime.now(timezone.utc).strftime("%Y-%m")
    manifest = {
        "version": manifest_date,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "embed_model": EMBED_MODEL_NAME,
        "embed_dim": EMBED_DIM,
        "dtype": "i8",
        "shards": {},
    }

    for ecosystem in ecosystems:
        manifest["shards"][ecosystem] = build_shard(ecosystem, out_dir, manifest_date, top_n)

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    logger.info(f"Wrote manifest to {manifest_path}")
    return manifest_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("dist/index"))
    parser.add_argument(
        "--ecosystems", default="python,npm,crates,go", help="comma-separated ecosystems"
    )
    parser.add_argument("--top-n", type=int, default=20_000)
    args = parser.parse_args()

    ecosystems = [e.strip() for e in args.ecosystems.split(",") if e.strip()]
    build_all(args.out, ecosystems, args.top_n)


if __name__ == "__main__":
    main()
