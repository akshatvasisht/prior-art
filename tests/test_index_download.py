"""Tests for core.index_download — first-use shard download + sig verification."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from priorart.core import index_download
from priorart.core.index_download import (
    INDEX_DIR_ENV,
    INDEX_URL_ENV,
    ShardPaths,
    _download,
    _verify_manifest_signature,
    ensure_manifest,
    ensure_shard,
    index_dir,
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_index_dir_respects_env_override(monkeypatch, tmp_path):
    target = tmp_path / "custom-idx"
    monkeypatch.setenv(INDEX_DIR_ENV, str(target))
    result = index_dir()
    assert result == target
    assert result.is_dir()


def test_index_dir_defaults_to_user_cache(monkeypatch, tmp_path):
    monkeypatch.delenv(INDEX_DIR_ENV, raising=False)
    monkeypatch.setattr(index_download, "user_cache_dir", lambda name: str(tmp_path / name))
    result = index_dir()
    assert result == tmp_path / "priorart" / "index"
    assert result.is_dir()


def test_download_via_url_override(monkeypatch, tmp_path):
    monkeypatch.setenv(INDEX_URL_ENV, "https://mirror.example/idx")

    class FakeStreamResponse:
        def __init__(self):
            self.status_code = 200

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield b"hello"
            yield b"-world"

    class FakeStreamCtx:
        def __enter__(self):
            return FakeStreamResponse()

        def __exit__(self, exc_type, exc, tb):
            return False

    captured = {}

    def fake_stream(method, url, follow_redirects=False):
        captured["method"] = method
        captured["url"] = url
        return FakeStreamCtx()

    import httpx

    monkeypatch.setattr(httpx, "stream", fake_stream)

    result = _download("manifest.json", tmp_path)

    assert result == tmp_path / "manifest.json"
    assert result.read_bytes() == b"hello-world"
    assert captured["url"] == "https://mirror.example/idx/manifest.json"
    assert captured["method"] == "GET"


def test_download_via_hf_hub(monkeypatch, tmp_path):
    monkeypatch.delenv(INDEX_URL_ENV, raising=False)

    fixture = tmp_path / "src-fixture.json"
    fixture.write_text('{"ok": true}')

    def fake_hf_hub_download(repo_id, filename, repo_type, cache_dir, local_dir):
        dest = Path(local_dir) / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(fixture.read_bytes())
        return str(dest)

    fake_module = MagicMock()
    fake_module.hf_hub_download = fake_hf_hub_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)

    result = _download("manifest.json", tmp_path)
    assert result.exists()
    assert result.read_text() == '{"ok": true}'


def test_download_hf_hub_missing_raises(monkeypatch, tmp_path):
    monkeypatch.delenv(INDEX_URL_ENV, raising=False)
    # Setting to None forces ImportError on `from huggingface_hub import ...`.
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)

    with pytest.raises(RuntimeError, match=INDEX_URL_ENV):
        _download("manifest.json", tmp_path)


def test_ensure_manifest_verifies_and_returns_json(monkeypatch, tmp_path):
    monkeypatch.setenv(INDEX_DIR_ENV, str(tmp_path))

    manifest_data = {"version": "2026-05", "shards": {}}

    def fake_download(name, dest_dir):
        path = dest_dir / name
        if name == "manifest.json":
            path.write_text(json.dumps(manifest_data))
        else:
            path.write_text("dummy-bundle")
        return path

    monkeypatch.setattr(index_download, "_download", fake_download)
    monkeypatch.setattr(index_download, "_verify_manifest_signature", lambda m, b: None)

    result = ensure_manifest()
    assert result == manifest_data


def test_ensure_manifest_deletes_on_verify_failure(monkeypatch, tmp_path):
    monkeypatch.setenv(INDEX_DIR_ENV, str(tmp_path))

    def fake_download(name, dest_dir):
        path = dest_dir / name
        path.write_text("{}" if name == "manifest.json" else "bundle")
        return path

    def boom(*args, **kwargs):
        raise ValueError("bad sig")

    monkeypatch.setattr(index_download, "_download", fake_download)
    monkeypatch.setattr(index_download, "_verify_manifest_signature", boom)

    with pytest.raises(RuntimeError, match="signature verification failed"):
        ensure_manifest()

    assert not (tmp_path / "manifest.json").exists()


def test_ensure_manifest_calls_download_for_bundle_too(monkeypatch, tmp_path):
    monkeypatch.setenv(INDEX_DIR_ENV, str(tmp_path))
    downloaded = []

    def fake_download(name, dest_dir):
        downloaded.append(name)
        path = dest_dir / name
        path.write_text("{}" if name == "manifest.json" else "bundle")
        return path

    monkeypatch.setattr(index_download, "_download", fake_download)
    monkeypatch.setattr(index_download, "_verify_manifest_signature", lambda m, b: None)

    ensure_manifest()
    assert "manifest.json" in downloaded
    assert "manifest.sigstore.json" in downloaded


def test_ensure_shard_success(monkeypatch, tmp_path):
    monkeypatch.setenv(INDEX_DIR_ENV, str(tmp_path))

    usearch_bytes = b"usearch-content"
    metadata_bytes = b'{"key": 0, "name": "requests"}\n'

    manifest = {
        "version": "2026-05",
        "shards": {
            "python": {
                "usearch": "python.usearch",
                "metadata": "python.jsonl",
                "usearch_sha256": _sha256_bytes(usearch_bytes),
                "metadata_sha256": _sha256_bytes(metadata_bytes),
            }
        },
    }

    def fake_download(name, dest_dir):
        path = dest_dir / name
        if name == "python.usearch":
            path.write_bytes(usearch_bytes)
        else:
            path.write_bytes(metadata_bytes)
        return path

    monkeypatch.setattr(index_download, "_download", fake_download)

    result = ensure_shard("python", manifest=manifest)
    assert isinstance(result, ShardPaths)
    assert result.usearch_path == tmp_path / "python.usearch"
    assert result.metadata_path == tmp_path / "python.jsonl"
    assert result.manifest_version == "2026-05"


def test_ensure_shard_sha_mismatch_raises_and_unlinks(monkeypatch, tmp_path):
    monkeypatch.setenv(INDEX_DIR_ENV, str(tmp_path))

    manifest = {
        "version": "1",
        "shards": {
            "python": {
                "usearch": "python.usearch",
                "metadata": "python.jsonl",
                "usearch_sha256": "deadbeef" * 8,  # wrong
                "metadata_sha256": _sha256_bytes(b"meta"),
            }
        },
    }

    def fake_download(name, dest_dir):
        path = dest_dir / name
        path.write_bytes(b"usearch-content" if "usearch" in name else b"meta")
        return path

    monkeypatch.setattr(index_download, "_download", fake_download)

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        ensure_shard("python", manifest=manifest)

    assert not (tmp_path / "python.usearch").exists()


def test_ensure_shard_missing_ecosystem_raises(monkeypatch, tmp_path):
    monkeypatch.setenv(INDEX_DIR_ENV, str(tmp_path))
    manifest = {"version": "1", "shards": {"npm": {}}}

    with pytest.raises(RuntimeError, match="Available: \\['npm'\\]"):
        ensure_shard("python", manifest=manifest)


def test_ensure_shard_no_expected_hash_skips_check(monkeypatch, tmp_path):
    monkeypatch.setenv(INDEX_DIR_ENV, str(tmp_path))

    manifest = {
        "version": "1",
        "shards": {
            "python": {
                "usearch": "python.usearch",
                "metadata": "python.jsonl",
                # no sha fields
            }
        },
    }

    def fake_download(name, dest_dir):
        path = dest_dir / name
        path.write_bytes(b"anything")
        return path

    monkeypatch.setattr(index_download, "_download", fake_download)

    result = ensure_shard("python", manifest=manifest)
    assert result.usearch_path.exists()
    assert result.metadata_path.exists()


def test_verify_manifest_signature_no_sigstore_is_noop(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "sigstore.models", None)
    monkeypatch.setitem(sys.modules, "sigstore.verify", None)
    manifest_path = tmp_path / "manifest.json"
    bundle_path = tmp_path / "manifest.sigstore.json"
    manifest_path.write_text("{}")
    bundle_path.write_text("{}")
    # Should not raise.
    _verify_manifest_signature(manifest_path, bundle_path)


def test_verify_manifest_signature_partial_sigstore_is_noop(monkeypatch, tmp_path):
    """Covers the second import line: models present, verify missing."""
    import types

    fake_models = types.ModuleType("sigstore.models")
    fake_models.Bundle = object  # placeholder
    fake_sigstore = types.ModuleType("sigstore")
    monkeypatch.setitem(sys.modules, "sigstore", fake_sigstore)
    monkeypatch.setitem(sys.modules, "sigstore.models", fake_models)
    monkeypatch.setitem(sys.modules, "sigstore.verify", None)

    manifest_path = tmp_path / "manifest.json"
    bundle_path = tmp_path / "manifest.sigstore.json"
    manifest_path.write_text("{}")
    bundle_path.write_text("{}")
    _verify_manifest_signature(manifest_path, bundle_path)
