"""
Build per-ecosystem usearch shards + manifest for the priorart semantic index.

Pipeline:

1. Fetch package records per ecosystem (``fetch.fetch_ecosystem``).
2. Embed ``"{name}: {description}"`` with BAAI/bge-small-en-v1.5 (fastembed).
3. L2-normalize and int8-quantize (scale 127).
4. Build a usearch HNSW index (``dtype="i8"``, cosine), save.
5. Write ``metadata.jsonl`` sidecar with
   `{key, name, registry, description, github_url, popularity, content_hash}`.
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
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .fetch import fetch_ecosystem

logger = logging.getLogger(__name__)

EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384

# Peak CPU throughput for quantized BGE-small is measured at batch=128
# (Intel/Haystack fastRAG benchmark); larger batches trade throughput for
# memory without speed gains on low-vCPU runners.
EMBED_BATCH_SIZE = 128

# Intra-op thread count for a single ONNX Runtime session. fastembed's
# parallel=N spawns N processes that each run ORT across all cores, which
# oversubscribes a small runner; we run one process (parallel=None) and pin
# ORT's intra-op pool to the vCPU count instead.
EMBED_THREADS = 4


def _embed_text(name: str, description: str) -> str:
    """The exact string embedded per record. Single source of truth so the
    ``texts`` list and ``_content_hash`` can never drift apart."""
    return f"{name}: {description}"


def _content_hash(name: str, description: str) -> str:
    """Stable identity for an embedded record's *input*.

    Keyed by model name + dim + dtype + the embedded text, so swapping the
    model or dimension invalidates every prior int8 vector and forces a full
    re-embed. The text portion byte-matches :func:`_embed_text`.
    """
    payload = f"{EMBED_MODEL_NAME}|{EMBED_DIM}|i8|{_embed_text(name, description)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Per-ecosystem cap on the top-N most-popular packages indexed in each shard.
# Calibrated against the reverse-dependency distribution per ecosystem. The flat
# 20K under-covered npm (51.8% of relevant packages missing per the npm coverage
# analysis) and over-covered crates/python (rank 20K had only 2 dependents —
# noise floor).
#
# Maven and NuGet use 20_000 as a fallback because their distributions weren't
# analyzed; revisit when the threshold validation runs against those ecosystems.
DEFAULT_TOP_N = {
    "npm": 50_000,
    "crates": 12_000,
    "python": 15_000,
    "go": 20_000,
    "maven": 20_000,
    "nuget": 20_000,
}
FALLBACK_TOP_N = 20_000


def _resolve_top_n(ecosystem: str, override: int | None) -> int:
    """Return the cap for ``ecosystem`` — explicit ``--top-n`` wins, else per-eco default.

    A non-positive override is rejected rather than silently honored: a negative
    cap would slice the popularity list as ``rows[:-n]`` and quietly index the
    wrong packages, and 0 would fetch nothing.
    """
    if override is not None:
        if override <= 0:
            raise ValueError(f"--top-n must be a positive integer, got {override}")
        return override
    return DEFAULT_TOP_N.get(ecosystem, FALLBACK_TOP_N)


def _positive_int(value: str) -> int:
    """argparse type for ``--top-n``: reject 0/negative at parse time."""
    n = int(value)
    if n <= 0:
        raise argparse.ArgumentTypeError(f"--top-n must be a positive integer, got {n}")
    return n


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


def build_shard(ecosystem: str, out_dir: Path, top_n: int | None = None) -> dict:
    """Build one shard's files. Returns the manifest entry for it.

    ``top_n=None`` resolves to the per-ecosystem default in ``DEFAULT_TOP_N``.
    """
    from usearch.index import Index  # type: ignore

    top_n = _resolve_top_n(ecosystem, top_n)
    logger.info(f"Building shard for {ecosystem} (top_n={top_n})")
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

    # Guard against shipping a degraded shard when the upstream registry
    # source collapses mid-fetch. Half of top_n is the smallest count we
    # can still defend as a useful slice; below that we fail the job so
    # `needs: build-shard` blocks the publish step and the prior index
    # remains authoritative.
    min_records = max(top_n // 2, 1000)
    if len(records) < min_records:
        raise RuntimeError(
            f"{ecosystem}: only {len(records)} records fetched "
            f"(required >= {min_records}). Upstream registry source "
            f"likely unavailable; aborting to preserve prior index."
        )

    import time

    from fastembed import TextEmbedding  # type: ignore (local import retained below)

    # Incremental build: reuse the prior int8 vector for any record whose
    # embedded text (and model/dim) is unchanged. Prior state comes from the
    # last *published* shard on HF Hub, downloaded into a scratch dir that is
    # disjoint from out_dir so it can't be swept into the upload.
    scratch_dir = out_dir.parent / ".prior-vectors" / ecosystem
    prior_vectors = _load_prior_vectors(scratch_dir, ecosystem)

    index = Index(ndim=EMBED_DIM, metric="cos", dtype="i8")

    # Compute each record's content hash once, deciding reuse vs. embed.
    hashes = [_content_hash(r["name"], r["description"]) for r in records]
    miss_indices = [i for i, h in enumerate(hashes) if h not in prior_vectors]
    reused_count = len(records) - len(miss_indices)
    total = len(records)
    logger.info(
        f"{ecosystem}: reusing {reused_count} prior vectors, "
        f"embedding {len(miss_indices)} new/changed"
    )

    # Only embed the misses; build the texts list from those records alone.
    cpu_count = os.cpu_count()
    embedded_count = 0
    embed_wall = 0.0
    new_vectors: dict[int, object] = {}
    if miss_indices:
        miss_texts = [
            _embed_text(records[i]["name"], records[i]["description"]) for i in miss_indices
        ]
        # parallel=None → single process; ORT spreads work across all cores
        # using the intra-op thread pool pinned at EMBED_THREADS.
        model = TextEmbedding(model_name=EMBED_MODEL_NAME, threads=EMBED_THREADS)
        embed_start = time.monotonic()
        for n, (i, vec) in enumerate(
            zip(
                miss_indices,
                model.embed(miss_texts, batch_size=EMBED_BATCH_SIZE, parallel=None),
                strict=True,
            )
        ):
            new_vectors[i] = _quantize_int8_row(vec)
            embedded_count += 1
            if (n + 1) % 1000 == 0:
                logger.info(f"{ecosystem}: embedded {n + 1}/{len(miss_indices)} new")
        embed_wall = time.monotonic() - embed_start

    # Single pass: add every record's vector (reused or freshly embedded) and
    # write its metadata line, including the content_hash for next time.
    with metadata_path.open("w", encoding="utf-8") as meta_f:
        for key, rec in enumerate(records):
            h = hashes[key]
            vec = new_vectors.get(key)
            if vec is None:
                vec = prior_vectors[h]
            index.add(key, vec)
            meta_f.write(
                json.dumps(
                    {
                        "key": key,
                        "name": rec["name"],
                        "registry": rec["registry"],
                        "description": rec["description"],
                        "github_url": rec.get("github_url"),
                        "popularity": rec.get("popularity", 0),
                        "content_hash": h,
                    }
                )
                + "\n"
            )

    index.save(str(usearch_path))

    docs_per_sec = (embedded_count / embed_wall) if embed_wall > 0 else 0.0
    logger.info(
        f"{ecosystem}: reused={reused_count} embedded={embedded_count} "
        f"total={total} docs/sec={docs_per_sec:.1f} cpu_count={cpu_count} "
        f"embed_wall={embed_wall:.1f}s"
    )

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


def build_all(out_dir: Path, ecosystems: list[str], top_n: int | None = None) -> Path:
    """Build all shards + write manifest.json (single-node flow).

    ``top_n=None`` resolves per-ecosystem via ``DEFAULT_TOP_N``; an explicit
    integer overrides for *all* ecosystems.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = _manifest_skeleton()
    for ecosystem in ecosystems:
        manifest["shards"][ecosystem] = build_shard(ecosystem, out_dir, top_n)
    return _write_manifest(manifest, out_dir)


def _download_prior_shard(scratch_dir: Path, ecosystem: str) -> tuple[Path, Path] | None:
    """Download the prior published ``{eco}.usearch`` + ``{eco}.metadata.jsonl``
    for ``ecosystem`` from the HF Hub dataset into ``scratch_dir``.

    Returns ``(usearch_path, metadata_path)`` pointing at the downloaded files,
    or ``None`` on any failure (huggingface_hub missing, network error, no
    prior manifest entry, legacy shard). ``scratch_dir`` is deliberately *not*
    the build's ``out_dir`` so the prior files can never collide with the
    freshly-built shard.
    """
    try:
        from huggingface_hub import hf_hub_download  # type: ignore
    except ImportError:
        logger.warning("huggingface_hub not installed; cannot fetch prior shards")
        return None

    repo_id = os.environ.get("HF_REPO_ID", "priorart/package-index")
    token = os.environ.get("HF_TOKEN") or None
    # Cache outside out_dir: on the assemble path scratch_dir == out_dir, and a
    # cache under it would be swept into the published upload.
    cache_dir = str(Path(tempfile.gettempdir()) / "priorart-hf-prior-cache")

    try:
        manifest_path = hf_hub_download(
            repo_id=repo_id,
            filename="manifest.json",
            repo_type="dataset",
            cache_dir=cache_dir,
            token=token,
        )
        prior = json.loads(Path(manifest_path).read_text())
        prior_entry = prior.get("shards", {}).get(ecosystem)
        if not prior_entry:
            logger.warning("prior manifest has no entry for %s", ecosystem)
            return None

        scratch_dir.mkdir(parents=True, exist_ok=True)
        dests: list[Path] = []
        for fname, sha_key in (
            (prior_entry["usearch"], "usearch_sha256"),
            (prior_entry["metadata"], "metadata_sha256"),
        ):
            local = hf_hub_download(
                repo_id=repo_id,
                filename=fname,
                repo_type="dataset",
                cache_dir=cache_dir,
                token=token,
            )
            # Strip any directory component so a manifest can't write outside
            # scratch_dir, then verify integrity before the binary is restored.
            dest = scratch_dir / Path(fname).name
            shutil.copyfile(local, dest)
            expected = prior_entry.get(sha_key)
            if expected and _sha256(dest) != expected:
                logger.warning(
                    "prior %s shard %s failed its manifest sha256; skipping reuse",
                    ecosystem,
                    fname,
                )
                return None
            dests.append(dest)

        return (dests[0], dests[1])
    except Exception as e:
        logger.warning("could not download prior shard for %s: %s", ecosystem, e)
        return None


def _prior_manifest_entry(scratch_dir: Path, ecosystem: str) -> dict | None:
    """Re-read the prior manifest entry (sha256s, record_count, version stamps)
    after the shard files have been downloaded. Returns ``None`` on any failure.
    """
    try:
        from huggingface_hub import hf_hub_download  # type: ignore
    except ImportError:
        return None

    repo_id = os.environ.get("HF_REPO_ID", "priorart/package-index")
    token = os.environ.get("HF_TOKEN") or None
    # Cache outside out_dir: on the assemble path scratch_dir == out_dir, and a
    # cache under it would be swept into the published upload.
    cache_dir = str(Path(tempfile.gettempdir()) / "priorart-hf-prior-cache")
    try:
        manifest_path = hf_hub_download(
            repo_id=repo_id,
            filename="manifest.json",
            repo_type="dataset",
            cache_dir=cache_dir,
            token=token,
        )
        prior = json.loads(Path(manifest_path).read_text())
        prior_entry = prior.get("shards", {}).get(ecosystem)
        if not prior_entry:
            return None
        return {
            **prior_entry,
            "stale_from_version": prior.get("version"),
            "stale_from_created_at": prior.get("created_at"),
        }
    except Exception:
        return None


def _fetch_prior_shard(out_dir: Path, ecosystem: str) -> dict | None:
    """Download the prior shard for ``ecosystem`` from the published HF Hub
    dataset and copy it into ``out_dir``. Returns the prior manifest entry
    (preserving sha256s and record_count) on success, ``None`` on any failure.

    Used by :func:`assemble_manifest` to reuse a prior shard for an ecosystem
    that was not rebuilt this run.
    """
    # Download into out_dir directly — assemble wants the shard files *in*
    # out_dir so the manifest can point at them; the int8-reuse path uses a
    # separate scratch dir instead.
    downloaded = _download_prior_shard(out_dir, ecosystem)
    if downloaded is None:
        return None
    return _prior_manifest_entry(out_dir, ecosystem)


def _load_prior_vectors(scratch_dir: Path, ecosystem: str):
    """Return ``{content_hash: int8_vector}`` from the prior published shard.

    Downloads the prior shard into ``scratch_dir``, restores its usearch index,
    and joins each prior metadata record's ``content_hash`` to its int8 vector
    (read back losslessly via ``index.get(key)`` — verified bit-exact for the
    ``dtype="i8"`` index). Legacy records without ``content_hash`` are skipped,
    so they simply won't be reused. Returns ``{}`` on any failure.
    """
    import numpy as np
    from usearch.index import Index  # type: ignore

    downloaded = _download_prior_shard(scratch_dir, ecosystem)
    if downloaded is None:
        return {}
    usearch_path, metadata_path = downloaded

    try:
        index = Index.restore(str(usearch_path), view=False)
    except Exception as e:
        logger.warning("could not restore prior index for %s: %s", ecosystem, e)
        return {}

    reuse: dict[str, object] = {}
    try:
        with metadata_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ch = rec.get("content_hash")
                key = rec.get("key")
                if not ch or not isinstance(key, int):
                    continue
                vec = index.get(key)
                if vec is None:
                    continue
                reuse[ch] = np.asarray(vec).astype(np.int8).ravel()
    except Exception as e:
        logger.warning("could not read prior metadata for %s: %s", ecosystem, e)
        return {}

    logger.info("%s: loaded %d reusable prior vectors", ecosystem, len(reuse))
    return reuse


def assemble_manifest(out_dir: Path, ecosystems: list[str]) -> Path:
    """Assemble manifest.json from shard files, reusing prior shards from
    HF Hub for any ecosystem not rebuilt this run.

    Refuses to publish if zero shards were rebuilt — total upstream collapse
    must preserve the prior published index, not republish a fully-stale copy.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = _manifest_skeleton()
    fresh: list[str] = []
    stale: list[str] = []

    for ecosystem in ecosystems:
        usearch_path, metadata_path = _shard_paths(out_dir, ecosystem)
        if usearch_path.exists() and metadata_path.exists():
            with metadata_path.open("r", encoding="utf-8") as f:
                record_count = sum(1 for line in f if line.strip())
            manifest["shards"][ecosystem] = {
                "usearch": usearch_path.name,
                "metadata": metadata_path.name,
                "usearch_sha256": _sha256(usearch_path),
                "metadata_sha256": _sha256(metadata_path),
                "record_count": record_count,
            }
            fresh.append(ecosystem)
            continue

        prior_entry = _fetch_prior_shard(out_dir, ecosystem)
        if prior_entry is None:
            raise FileNotFoundError(
                f"No fresh build and no prior shard available for {ecosystem}: "
                f"expected {usearch_path.name} and {metadata_path.name} in {out_dir}"
            )
        manifest["shards"][ecosystem] = prior_entry
        stale.append(ecosystem)

    if not fresh:
        raise RuntimeError(
            "Refusing to publish: no shards were rebuilt this run. "
            "Total upstream collapse — preserving the prior published index."
        )

    if stale:
        manifest["stale_shards"] = stale
        logger.warning("Publishing with stale shards: %s", stale)

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
    parser.add_argument(
        "--top-n",
        type=_positive_int,
        default=None,
        help="override per-ecosystem cap (default: per-ecosystem from DEFAULT_TOP_N)",
    )
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
