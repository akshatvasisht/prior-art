"""
Build per-ecosystem usearch shards + manifest for the priorart semantic index.

Pipeline:

1. Fetch package records per ecosystem (``fetch.fetch_ecosystem``).
2. Embed ``"{name}: {description}"`` with BAAI/bge-small-en-v1.5 (fastembed).
3. L2-normalize and int8-quantize (scale 127).
4. Build a usearch HNSW index (``dtype="i8"``, cosine), save.
5. Write ``metadata.jsonl`` sidecar with `{key, name, registry, description, github_url}`.
6. SHA-256 each shard file; in assemble mode, stitch shard SHAs into ``manifest.json``.

Sigstore signing of the manifest happens in CI (``sigstore sign``), not here —
this script is safe to run locally to smoke-test outputs.

Three invocation modes:

    # Single ecosystem (matrix worker)
    python -m scripts.index_build.build --ecosystem python --out dist/index --top-n 20000

    # All ecosystems + manifest (single-node)
    python -m scripts.index_build.build --ecosystems python,npm,crates,go --out dist/index --top-n 20000

    # Assemble manifest from already-built shards (matrix finalizer)
    python -m scripts.index_build.build --assemble --ecosystems python,npm,crates,go --out dist/index
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

# Batch size handed to fastembed. 512 amortizes kernel-dispatch overhead better
# than 256 on CPU ORT with the small BGE model; memory cost is ~1.5 MB/batch.
EMBED_BATCH_SIZE = 512


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _quantize_int8_row(vector):
    """Unit-normalize a single float32 row then scale by 127 → int8."""
    import numpy as np

    arr = np.asarray(vector, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return np.clip(np.round(arr * 127.0), -127, 127).astype(np.int8)


def _shard_paths(out_dir: Path, ecosystem: str) -> tuple[Path, Path]:
    return (
        out_dir / f"{ecosystem}.usearch",
        out_dir / f"{ecosystem}.metadata.jsonl",
    )


def build_shard(ecosystem: str, out_dir: Path, top_n: int) -> dict:
    """Build one shard's files. Returns the manifest entry for it."""
    from fastembed import TextEmbedding  # type: ignore
    from usearch.index import Index  # type: ignore

    logger.info(f"Building shard for {ecosystem}")
    out_dir.mkdir(parents=True, exist_ok=True)

    usearch_path, metadata_path = _shard_paths(out_dir, ecosystem)

    seen: set[tuple[str, str]] = set()
    records: list[dict] = []
    for rec in fetch_ecosystem(ecosystem, top_n=top_n):
        key = (rec["name"], rec["registry"])
        if key in seen:
            continue
        seen.add(key)
        records.append(rec)

    logger.info(f"{ecosystem}: {len(records)} unique records to embed")

    model = TextEmbedding(model_name=EMBED_MODEL_NAME)
    index = Index(ndim=EMBED_DIM, metric="cos", dtype="i8")

    texts = [f"{r['name']}: {r['description']}" for r in records]
    # Stream embeddings through the index + metadata writer in a single pass.
    # Fastembed handles batching internally; ONNX Runtime threads across cores.
    with metadata_path.open("w", encoding="utf-8") as meta_f:
        for key, (rec, vec) in enumerate(
            zip(records, model.embed(texts, batch_size=EMBED_BATCH_SIZE), strict=True)
        ):
            index.add(key, _quantize_int8_row(vec))
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


def _manifest_skeleton() -> dict:
    return {
        "version": datetime.now(timezone.utc).strftime("%Y-%m"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "embed_model": EMBED_MODEL_NAME,
        "embed_dim": EMBED_DIM,
        "dtype": "i8",
        "shards": {},
    }


def _write_manifest(manifest: dict, out_dir: Path) -> Path:
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    logger.info(f"Wrote manifest to {manifest_path}")
    return manifest_path


def build_all(out_dir: Path, ecosystems: list[str], top_n: int) -> Path:
    """Build all shards + write manifest.json (single-node flow)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = _manifest_skeleton()
    for ecosystem in ecosystems:
        manifest["shards"][ecosystem] = build_shard(ecosystem, out_dir, top_n)
    return _write_manifest(manifest, out_dir)


def assemble_manifest(out_dir: Path, ecosystems: list[str]) -> Path:
    """Assemble manifest.json from already-built shard files (matrix finalizer)."""
    manifest = _manifest_skeleton()
    for ecosystem in ecosystems:
        usearch_path, metadata_path = _shard_paths(out_dir, ecosystem)
        if not usearch_path.exists() or not metadata_path.exists():
            raise FileNotFoundError(
                f"Missing shard files for {ecosystem}: "
                f"expected {usearch_path.name} and {metadata_path.name} in {out_dir}"
            )
        with metadata_path.open("r", encoding="utf-8") as f:
            record_count = sum(1 for line in f if line.strip())
        manifest["shards"][ecosystem] = {
            "usearch": usearch_path.name,
            "metadata": metadata_path.name,
            "usearch_sha256": _sha256(usearch_path),
            "metadata_sha256": _sha256(metadata_path),
            "record_count": record_count,
        }
    return _write_manifest(manifest, out_dir)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("dist/index"))
    parser.add_argument("--ecosystem", help="build a single ecosystem (matrix worker mode)")
    parser.add_argument(
        "--ecosystems",
        default="python,npm,crates,go",
        help="comma-separated ecosystems (single-node or --assemble mode)",
    )
    parser.add_argument("--top-n", type=int, default=20_000)
    parser.add_argument(
        "--assemble",
        action="store_true",
        help="assemble manifest.json from pre-built shards, don't rebuild",
    )
    args = parser.parse_args()

    if args.assemble:
        ecosystems = [e.strip() for e in args.ecosystems.split(",") if e.strip()]
        assemble_manifest(args.out, ecosystems)
        return

    if args.ecosystem:
        args.out.mkdir(parents=True, exist_ok=True)
        build_shard(args.ecosystem, args.out, args.top_n)
        return

    ecosystems = [e.strip() for e in args.ecosystems.split(",") if e.strip()]
    build_all(args.out, ecosystems, args.top_n)


if __name__ == "__main__":
    main()
