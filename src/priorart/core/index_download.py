"""
First-use download + signature verification for the priorart package index.

The index is a collection of per-ecosystem usearch shards hosted on a public
Hugging Face Hub dataset repo (``priorart/package-index``). Each shard ships
alongside a sigstore bundle pinned to the GitHub Actions workflow that built
it, so a poisoned shard would require compromising either the repo or
sigstore itself.

Layout on disk::

    ~/.cache/priorart/index/
      manifest.json
      manifest.sigstore.json
      python-2026-05.usearch
      python-2026-05.metadata.jsonl
      npm-2026-05.usearch
      ...

The ``metadata.jsonl`` sidecar stores ``{"key": int, "name": str, "registry": str,
"description": str, "github_url": str | null}`` for each indexed package, indexed
by the integer ``key`` stored in the usearch index.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from platformdirs import user_cache_dir

logger = logging.getLogger(__name__)

HF_REPO_ID = "priorart/package-index"
HF_REPO_TYPE = "dataset"
OIDC_ISSUER = "https://token.actions.githubusercontent.com"
SIGNER_IDENTITY = (
    "https://github.com/akshatvasisht/prior-art/.github/workflows/rebuild-index.yml@refs/heads/main"
)

# Override for mirrors / local testing
INDEX_URL_ENV = "PRIORART_INDEX_URL"
INDEX_DIR_ENV = "PRIORART_INDEX_DIR"


@dataclass
class ShardPaths:
    """Filesystem handles for a downloaded per-ecosystem shard."""

    usearch_path: Path
    metadata_path: Path
    manifest_version: str


def index_dir() -> Path:
    """Return the local index cache directory, creating it if missing."""
    override = os.environ.get(INDEX_DIR_ENV)
    base = Path(override) if override else Path(user_cache_dir("priorart")) / "index"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_manifest_signature(manifest_path: Path, bundle_path: Path) -> None:
    """Verify the manifest's sigstore bundle, pinning identity + issuer.

    Raises RuntimeError on mismatch. No-op if sigstore isn't installed
    (build environments without the optional extras).
    """
    try:
        from sigstore.models import Bundle  # type: ignore
        from sigstore.verify import Verifier, policy  # type: ignore
    except ImportError:
        logger.warning("sigstore not installed; skipping signature verification")
        return

    # Real sigstore verification requires production bundles; exercised in
    # integration, not unit tests.
    verifier = Verifier.production(offline=True)  # pragma: no cover
    bundle = Bundle.from_json(bundle_path.read_bytes())  # pragma: no cover
    pol = policy.Identity(identity=SIGNER_IDENTITY, issuer=OIDC_ISSUER)  # pragma: no cover
    verifier.verify_artifact(  # pragma: no cover
        input_=manifest_path.read_bytes(), bundle=bundle, policy=pol
    )


def _download(repo_filename: str, dest_dir: Path) -> Path:
    """Resolve ``repo_filename`` either from PRIORART_INDEX_URL or from HF Hub."""
    override = os.environ.get(INDEX_URL_ENV)
    dest = dest_dir / Path(repo_filename).name

    if override:
        import httpx

        url = override.rstrip("/") + "/" + repo_filename
        with httpx.stream("GET", url, follow_redirects=True) as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        return dest

    try:
        from huggingface_hub import hf_hub_download  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "huggingface_hub is required to download the priorart index. "
            "Install it with `pip install huggingface-hub` or set "
            f"{INDEX_URL_ENV} to a mirror serving raw files."
        ) from e

    return Path(
        hf_hub_download(
            repo_id=HF_REPO_ID,
            filename=repo_filename,
            repo_type=HF_REPO_TYPE,
            cache_dir=str(dest_dir / "hf-cache"),
            local_dir=str(dest_dir),
        )
    )


def ensure_manifest() -> dict[str, Any]:
    """Download + verify + return the index manifest."""
    dest = index_dir()
    manifest_path = _download("manifest.json", dest)
    bundle_path = _download("manifest.sigstore.json", dest)

    try:
        _verify_manifest_signature(manifest_path, bundle_path)
    except Exception as e:
        # Delete the unverified manifest so the next run re-fetches cleanly.
        manifest_path.unlink(missing_ok=True)
        raise RuntimeError(f"Index manifest signature verification failed: {e}") from e

    return json.loads(manifest_path.read_text())


def ensure_shard(ecosystem: str, manifest: dict[str, Any] | None = None) -> ShardPaths:
    """Ensure a per-ecosystem shard is downloaded and its SHA-256 matches the manifest."""
    manifest = manifest or ensure_manifest()
    shards = manifest.get("shards", {})
    if ecosystem not in shards:
        raise RuntimeError(
            f"Ecosystem '{ecosystem}' missing from index manifest. "
            f"Available: {sorted(shards.keys())}"
        )

    shard_info = shards[ecosystem]
    dest = index_dir()

    usearch_file = shard_info["usearch"]
    metadata_file = shard_info["metadata"]

    usearch_path = _download(usearch_file, dest)
    metadata_path = _download(metadata_file, dest)

    for path, expected_hash in (
        (usearch_path, shard_info.get("usearch_sha256")),
        (metadata_path, shard_info.get("metadata_sha256")),
    ):
        if not expected_hash:
            continue
        actual = _sha256(path)
        if actual != expected_hash:
            path.unlink(missing_ok=True)
            raise RuntimeError(
                f"SHA-256 mismatch for {path.name}: expected {expected_hash}, got {actual}"
            )

    return ShardPaths(
        usearch_path=usearch_path,
        metadata_path=metadata_path,
        manifest_version=manifest.get("version", "unknown"),
    )
