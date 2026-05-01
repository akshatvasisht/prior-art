# Pipeline runbook

Concrete steps to stand up the index-build pipeline from zero and produce a published, sigstore-verified HNSW shard that clients can download via `ensure_shard()`. Written for a maintainer who has never run this pipeline before.

Two GitHub Actions workflows are involved:

- `extract-snapshot.yml` — weekly probe of the ecosyste.ms open-data S3 dump; full streaming extraction only when a new dump publishes (~quarterly). Output: slim per-ecosystem JSONL on `priorart/package-snapshot`.
- `rebuild-index.yml` — monthly cron. Reads the snapshot for the popularity lane, adds the recency lane from the live API, embeds, builds HNSW shards, sigstore-signs the manifest, publishes to `priorart/package-index`.

Everything here is one-time setup except §3 (dispatch) and §4 (verify).

---

## 1. Hugging Face dataset repo

### 1.1 Create the repo

The workflow calls `api.create_repo(..., exist_ok=True)`, so the repo auto-creates on first run. If you want to own the naming ahead of time:

```bash
pip install huggingface_hub
hf auth login                         # paste a write token
hf repos create priorart/package-index --repo-type dataset
```

> The CLI was renamed from `huggingface-cli` to `hf` in mid-2025. The legacy binary still works but prints a deprecation warning pointing at the new command.

Alternative: https://huggingface.co/new-dataset (web UI), namespace `priorart`, name `package-index`, visibility public.

### 1.2 Mint a token for CI

1. Go to https://huggingface.co/settings/tokens.
2. "New token" → **Fine-grained**.
3. Repository permissions: `priorart/package-index` → **Read and write to contents/settings**.
4. Name: `github-actions-rebuild-index`.
5. Copy the token (only shown once).

Add it as a GitHub repository secret named `HF_TOKEN`:

- Settings → Secrets and variables → Actions → New repository secret.
- Name `HF_TOKEN`, value = the token.

Classic write tokens also work but grant access to every repo on your HF account. Avoid for CI.

**`HF_TOKEN` is the only secret the workflows need.** Both `rebuild-index.yml` and `extract-snapshot.yml` use the same token. No GCP project, no service account, no Workload Identity Federation — package metadata comes from the ecosyste.ms open-data S3 dump (popularity lane, CC-BY-SA 4.0) and live API (recency lane, free, unauthenticated). Earlier versions of this runbook required a BigQuery-backed deps.dev pipeline; that path was replaced because `bigquery-public-data.deps_dev_v1.PackageVersions` is not meaningfully partitioned, pushing every ranking query past the 1 TiB free-tier cap.

**Two HF datasets are involved:**
- `priorart/package-snapshot` — slim per-ecosystem JSONL extracted from the dump. Populated by `extract-snapshot.yml`.
- `priorart/package-index` — sigstore-signed HNSW shards. Populated by `rebuild-index.yml`, consumed by clients via `ensure_shard()`.

The token needs read+write on both. Mint one fine-grained token scoped to both repos.

---

## 2. Sigstore signer identity pin

`src/priorart/core/index_download.py` pins the expected signer:

```python
SIGNER_IDENTITY = "https://github.com/akshatvasisht/prior-art/.github/workflows/rebuild-index.yml@refs/heads/main"
OIDC_ISSUER    = "https://token.actions.githubusercontent.com"
```

The pin *will* break if any of the following change:

- Workflow file renamed (e.g. `rebuild-index.yml` → `build-index.yml`).
- Workflow moved (e.g. into `.github/workflows/index/`).
- Workflow dispatched from a non-`main` branch — `@refs/heads/main` becomes `@refs/heads/<branch>` or `@refs/tags/<tag>`.
- Repo renamed or transferred to a different owner.

Error surfaced client-side: `VerificationError: Certificate's SANs do not match expected identity`.

If the workflow needs to change name or path, bump the pin in `index_download.py` in the same PR that renames the workflow, then merge to main *before* dispatching.

---

## 3. Dispatch the build

Navigate to the repo's Actions tab → "Rebuild package index" → **Run workflow** → branch `main` → leave `top_n` at `20000` → Run.

Expected wall time: **~15–20 minutes.** The workflow fans out into a **matrix** of six parallel `build-shard` jobs (one per ecosystem) followed by a single `assemble-and-publish` job.

### Job graph

```
┌─ build-shard (python) ─┐
├─ build-shard (npm)    ─┤
├─ build-shard (crates) ─┤
├─ build-shard (go)     ─┼─→ assemble-and-publish
├─ build-shard (maven)  ─┤
└─ build-shard (nuget)  ─┘
```

Each `build-shard` job (~7–10 min):
1. Read popularity slice from the slim JSONL on `priorart/package-snapshot` (downloaded via `huggingface_hub`, ~few hundred MB), top-N by `dependent_packages_count` or `downloads`.
2. Top up with the recency slice from the live ecosyste.ms API (`sort=latest_release_published_at`, ~2 pages per ecosystem).
3. Embed with `fastembed` / `bge-small-en-v1.5` in streaming mode (`batch_size=512`, ONNX Runtime threads across all cores).
4. Build HNSW shard with `usearch`, int8-quantize.
5. Upload the two shard files (`{ecosystem}.usearch`, `{ecosystem}.metadata.jsonl`) as an artifact named `shard-{ecosystem}`.

> **Bootstrap.** `priorart/package-snapshot` must exist before `rebuild-index.yml` can run successfully. Dispatch `extract-snapshot.yml` once on a fresh setup; subsequent rebuilds reuse whatever snapshot is current.

`assemble-and-publish` (~3–5 min):
1. Download all six shard artifacts, merge into `dist/index/`.
2. Compute SHA-256s + record counts, write `manifest.json` via `python -m scripts.index_build.build --assemble ...`.
3. Sigstore-sign the manifest via GH OIDC.
4. Upload `dist/index/` to `priorart/package-index` on HF Hub.

Artifacts: each per-ecosystem `shard-*` artifact plus the final `index-build-output` are retained for 7 days — download and inspect on failure.

### Running locally (single-node)

The old single-node flow still works for local testing:

```bash
python -m scripts.index_build.build --ecosystems python,npm,crates,go,maven,nuget --out dist/index --top-n 20000
```

This runs all six ecosystems sequentially and writes `manifest.json` in one shot. Useful for local smoke-testing; CI always uses the matrix.

---

## 4. Verification

After the workflow succeeds:

### 4.1 Inspect the HF dataset

https://huggingface.co/datasets/priorart/package-index/tree/main should show:

```
manifest.json
manifest.sigstore.json
python.usearch
python.metadata.jsonl
npm.usearch
npm.metadata.jsonl
crates.usearch
crates.metadata.jsonl
go.usearch
go.metadata.jsonl
maven.usearch
maven.metadata.jsonl
nuget.usearch
nuget.metadata.jsonl
```

Sanity-check file sizes — each shard should be roughly 30–90 MB at 20k packages × 384-dim int8.

### 4.2 End-to-end client check

From a clean machine (or `rm -rf ~/.cache/priorart/index/`):

```bash
pip install priorart-agent
priorart find --language python --task "http client"
```

Expected output includes a "Downloading package index..." progress line, sigstore verification passes silently, then top-5 packages appear. If sigstore fails, the CLI aborts with the identity-mismatch error.

### 4.3 Lite-mode bypass (sanity)

`priorart find --language python --task "http client" --lite` should return results without touching the shard. Useful to confirm the rest of the pipeline is healthy if the index has a problem.

---

## 5. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `RepositoryNotFoundError: priorart/package-snapshot` during fetch | Bootstrap missing. Dispatch `extract-snapshot.yml` once, then redispatch `rebuild-index.yml`. |
| `extract-snapshot.yml` extract step fails on `pg_restore` | Schema or pg_dump version drift in the new dump. Inspect the failed step's pipe; the error surfaces from `pg_restore` stderr. |
| `httpx.RequestError` on the recency lane | Transient ecosyste.ms outage. The fetcher retries 5× with exponential backoff; beyond that the recency lane returns empty and the build still ships using only the snapshot's popularity slice. |
| GH Actions fails during sigstore sign with `OIDC token not available` | Workflow missing `permissions: id-token: write`. Already present in `rebuild-index.yml`; only regresses if edited. |
| GH Actions fails during HF upload with `401 Unauthorized` | `HF_TOKEN` expired or missing write scope for `priorart/package-index`. Mint a new fine-grained token per §1.2. |
| GH Actions `assemble-and-publish` fails with `403 Forbidden` on `…/info/lfs/objects/batch` | `HF_TOKEN` has write on `priorart/package-snapshot` but not `priorart/package-index`. Fine-grained tokens scope per repo; setting write on the snapshot alone passes extract-snapshot but not the index publish. Re-mint with both repos selected and update the GitHub `HF_TOKEN` secret. |
| `build-shard ({eco})` killed mid-embed with "runner has received a shutdown signal" + "leaked semaphore objects" | Kernel OOM-killed fastembed worker processes. `EMBED_PARALLEL` controls how many ONNX sessions run in parallel; on a 4 vCPU / 7 GB `ubuntu-latest` runner, `0` (one-per-core) overshoots. Pin to `2` in `scripts/index_build/build.py`. |
| Hugging Face dataset preview shows `DatasetGenerationError: CastError ... column names don't match` for `priorart/package-index` | Cosmetic only; the runtime path is unaffected. The repo holds heterogeneous artifacts (binary shards + manifest + metadata.jsonl) and isn't loadable via `datasets.load_dataset`. The `assemble-and-publish` step writes a `README.md` with `viewer: false` to suppress this; if the banner persists, confirm that file uploaded. |
| Client CLI fails with `VerificationError: Certificate's SANs do not match expected identity` | Workflow dispatched from non-main branch, or `SIGNER_IDENTITY` pin drifted from the actual signer. Check `index_download.py` against the workflow path. |
| Client CLI fails with `sigstore not installed` | `sigstore` package not in runtime deps. Already pinned in `pyproject.toml`; confirm the installed wheel bundles it. |

---

## 6. Ongoing ops

- **Monthly cadence:** cron fires on the 1st of each month at 06:00 UTC. No manual action required once §1–§2 are set up.
- **If a monthly run fails:** the HF shard and the client-side cache are unchanged, so existing users are unaffected. Investigate from the failed Actions run; re-dispatch once fixed.
- **Breaking changes to the embedding model or index format:** bump a version tag on the HF dataset repo and update `index_download.py` to pin the expected manifest schema version. Old clients continue reading their cached shards until they clear cache.
- **Key rotation:** not required — sigstore keyless uses ephemeral certs tied to the OIDC token; no long-lived private key to rotate.
- **Attribution:** the `README.md` must credit ecosyste.ms under CC-BY-SA 4.0. Redistributed index shards inherit the same license — do not claim them as MIT-licensed data even though the priorart code itself is MIT.

---

## 7. Source-selection rationale

See `agentcontext/DECISIONS.md` §10 for why the popularity lane reads ecosyste.ms's S3 dump (not the live API, not deps.dev BigQuery), the trade-offs accepted, and the rejected alternatives.

---

## Appendix: one-shot setup checklist

```
[ ] §1.1  HF dataset repos exist at priorart/package-index AND priorart/package-snapshot
[ ] §1.2  HF_TOKEN (fine-grained, write on both datasets) added to GH secrets
[ ] §2    SIGNER_IDENTITY in index_download.py matches workflow path and owner
[ ] §3    extract-snapshot.yml dispatched once; priorart/package-snapshot populated
[ ] §3    rebuild-index.yml dispatched successfully from Actions tab
[ ] §4.1  HF dataset contains manifest.json + manifest.sigstore.json + per-ecosystem shards
[ ] §4.2  Fresh client can run `priorart find` with no cache and receive verified results
[ ] §6    README.md credits ecosyste.ms under CC-BY-SA 4.0
```

Once these eight boxes are checked, the monthly rebuild runs unattended and clients can download the shard.
