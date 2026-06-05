"""Tests for scripts.index_build.build.assemble_manifest — tolerant mode."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest


def _write_shard(out_dir: Path, ecosystem: str, n: int = 3) -> None:
    """Write a fake usearch + metadata.jsonl pair for `ecosystem`."""
    (out_dir / f"{ecosystem}.usearch").write_bytes(b"u" * 64)
    meta = out_dir / f"{ecosystem}.metadata.jsonl"
    with meta.open("w", encoding="utf-8") as f:
        for i in range(n):
            f.write(json.dumps({"key": i, "name": f"pkg{i}", "registry": ecosystem}) + "\n")


def test_assemble_all_fresh(tmp_path: Path):
    from scripts.index_build.build import assemble_manifest

    out = tmp_path / "index"
    out.mkdir()
    _write_shard(out, "python", n=5)
    _write_shard(out, "npm", n=7)

    manifest_path = assemble_manifest(out, ["python", "npm"])
    manifest = json.loads(manifest_path.read_text())

    assert set(manifest["shards"].keys()) == {"python", "npm"}
    assert manifest["shards"]["python"]["record_count"] == 5
    assert manifest["shards"]["npm"]["record_count"] == 7
    assert "stale_shards" not in manifest


def test_assemble_with_stale_fallback(tmp_path: Path):
    """When a shard is missing, fetch the prior shard from HF Hub."""
    from scripts.index_build import build as build_mod

    out = tmp_path / "index"
    out.mkdir()
    _write_shard(out, "python", n=5)

    prior_entry = {
        "usearch": "npm.usearch",
        "metadata": "npm.metadata.jsonl",
        "usearch_sha256": "abc",
        "metadata_sha256": "def",
        "record_count": 12345,
    }

    def _fake_fetch(out_dir: Path, ecosystem: str) -> dict | None:
        # Simulate the hf_hub_download side-effect of writing files into out_dir.
        (out_dir / "npm.usearch").write_bytes(b"prior")
        (out_dir / "npm.metadata.jsonl").write_bytes(b"")
        return {**prior_entry, "stale_from_version": "2026-04", "stale_from_created_at": "x"}

    with patch.object(build_mod, "_fetch_prior_shard", side_effect=_fake_fetch):
        manifest_path = build_mod.assemble_manifest(out, ["python", "npm"])

    manifest = json.loads(manifest_path.read_text())
    assert manifest["stale_shards"] == ["npm"]
    assert manifest["shards"]["npm"]["record_count"] == 12345
    assert manifest["shards"]["npm"]["stale_from_version"] == "2026-04"
    assert manifest["shards"]["python"]["record_count"] == 5


def test_assemble_refuses_fully_stale(tmp_path: Path):
    """If no shards were rebuilt this run, refuse to republish a stale manifest."""
    from scripts.index_build import build as build_mod

    out = tmp_path / "index"
    out.mkdir()

    with patch.object(
        build_mod, "_fetch_prior_shard", return_value={"usearch": "x", "metadata": "y"}
    ):
        with pytest.raises(RuntimeError, match="no shards were rebuilt"):
            build_mod.assemble_manifest(out, ["python", "npm"])


def test_assemble_no_prior_no_fresh_raises(tmp_path: Path):
    """Missing fresh shard AND failed prior-shard fetch is a hard failure."""
    from scripts.index_build import build as build_mod

    out = tmp_path / "index"
    out.mkdir()
    _write_shard(out, "python", n=3)

    with patch.object(build_mod, "_fetch_prior_shard", return_value=None):
        with pytest.raises(FileNotFoundError, match="No fresh build and no prior shard"):
            build_mod.assemble_manifest(out, ["python", "npm"])


def test_fetch_prior_shard_handles_missing_huggingface_hub(tmp_path: Path, monkeypatch):
    """ImportError on huggingface_hub returns None, doesn't crash."""
    import builtins

    from scripts.index_build import build as build_mod

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name == "huggingface_hub":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    assert build_mod._fetch_prior_shard(tmp_path, "python") is None


def test_fetch_prior_shard_returns_none_when_ecosystem_missing(tmp_path: Path, monkeypatch):
    """Prior manifest without the requested ecosystem returns None."""
    from scripts.index_build import build as build_mod

    out = tmp_path / "index"
    out.mkdir()
    prior_manifest = {"version": "2026-04", "shards": {"crates": {}}}

    fake_manifest_path = tmp_path / "manifest.json"
    fake_manifest_path.write_text(json.dumps(prior_manifest))

    def _fake_download(*, repo_id, filename, repo_type, cache_dir, token):
        return str(fake_manifest_path)

    fake_hf = type("FakeHF", (), {"hf_hub_download": staticmethod(_fake_download)})
    monkeypatch.setitem(__import__("sys").modules, "huggingface_hub", fake_hf)

    assert build_mod._fetch_prior_shard(out, "python") is None


def test_fetch_prior_shard_copies_files_and_returns_entry(tmp_path: Path, monkeypatch):
    """Happy path: prior manifest has the entry, files are copied, entry returned."""
    from scripts.index_build import build as build_mod

    out = tmp_path / "index"
    out.mkdir()

    usearch_bytes = b"prior-usearch"
    metadata_bytes = b"prior-metadata"
    prior_entry = {
        "usearch": "npm.usearch",
        "metadata": "npm.metadata.jsonl",
        "usearch_sha256": hashlib.sha256(usearch_bytes).hexdigest(),
        "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
        "record_count": 999,
    }
    prior_manifest = {
        "version": "2026-04",
        "created_at": "2026-04-01T00:00:00Z",
        "shards": {"npm": prior_entry},
    }

    cache_root = tmp_path / "hf-cache"
    cache_root.mkdir()
    manifest_blob = cache_root / "manifest.json"
    manifest_blob.write_text(json.dumps(prior_manifest))
    usearch_blob = cache_root / "npm.usearch"
    usearch_blob.write_bytes(usearch_bytes)
    metadata_blob = cache_root / "npm.metadata.jsonl"
    metadata_blob.write_bytes(metadata_bytes)

    def _fake_download(*, repo_id, filename, repo_type, cache_dir, token):
        return str(cache_root / filename)

    fake_hf = type("FakeHF", (), {"hf_hub_download": staticmethod(_fake_download)})
    monkeypatch.setitem(__import__("sys").modules, "huggingface_hub", fake_hf)

    result = build_mod._fetch_prior_shard(out, "npm")

    assert result is not None
    assert result["record_count"] == 999
    assert result["stale_from_version"] == "2026-04"
    assert (out / "npm.usearch").read_bytes() == b"prior-usearch"
    assert (out / "npm.metadata.jsonl").read_bytes() == b"prior-metadata"


def test_fetch_prior_shard_rejects_sha256_mismatch(tmp_path: Path, monkeypatch):
    """A downloaded shard whose bytes don't match the manifest sha256 is not
    reused — guards against a tampered binary being deserialized."""
    from scripts.index_build import build as build_mod

    prior_entry = {
        "usearch": "npm.usearch",
        "metadata": "npm.metadata.jsonl",
        "usearch_sha256": "0" * 64,  # deliberately wrong
        "metadata_sha256": "0" * 64,
        "record_count": 999,
    }
    cache_root = tmp_path / "hf-cache"
    cache_root.mkdir()
    (cache_root / "manifest.json").write_text(
        json.dumps({"version": "2026-04", "shards": {"npm": prior_entry}})
    )
    (cache_root / "npm.usearch").write_bytes(b"tampered")
    (cache_root / "npm.metadata.jsonl").write_bytes(b"tampered")

    def _fake_download(*, repo_id, filename, repo_type, cache_dir, token):
        return str(cache_root / filename)

    fake_hf = type("FakeHF", (), {"hf_hub_download": staticmethod(_fake_download)})
    monkeypatch.setitem(__import__("sys").modules, "huggingface_hub", fake_hf)

    assert build_mod._fetch_prior_shard(tmp_path / "index", "npm") is None


def test_fetch_prior_shard_swallows_network_error(tmp_path: Path, monkeypatch):
    """Any exception from hf_hub_download is logged and swallowed; returns None."""
    from scripts.index_build import build as build_mod

    def _fail(**_):
        raise RuntimeError("network down")

    fake_hf = type("FakeHF", (), {"hf_hub_download": staticmethod(_fail)})
    monkeypatch.setitem(__import__("sys").modules, "huggingface_hub", fake_hf)

    assert build_mod._fetch_prior_shard(tmp_path, "python") is None
