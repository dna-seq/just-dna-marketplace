# Agent Guidelines — just-dna-registry

This repo is the **annotation module registry**: a standalone, **server-side REST API
service** (FastAPI) that catalogs, versions, validates, and serves annotation modules for the
`just-dna-lite` ecosystem. **There is no frontend here** — the webui and Dagster pipelines are
*consumers* of this API. Any UI concern (Reflex, Fomantic, PRS widgets) belongs in `just-dna-lite`,
not in this repo.

The founding design document is **[docs/SPEC.md](docs/SPEC.md)** — read it first. It is the source
of truth for the manifest contract (§4), integrity mechanism (§5), versioning (§6), and the REST
interface (§8). This file (`CLAUDE.md`) is the *how we code* companion to that *what we build* spec.

---

## What this service does (one screen)

- **Catalog index** — a queryable projection (SQLite for MVP → Postgres) of every published
  `(namespace, name, version)`. The per-module `manifest.json` is the **source of truth**; the DB
  is a rebuildable projection of it.
- **Artifact storage** — HuggingFace Hub datasets (hybrid backend: free CDN + git-revision
  versioning + existing `HfFileSystem` discovery), or S3/MinIO keyed by digest.
- **Server-side compile/validate gate** — on publish the server runs `validate_spec()` and
  (default) `compile_module()` itself, so `compile_success`, input hashes, and artifact digests are
  produced by the trusted party and cannot be forged.
- **Integrity contract** — SHA-256 per input CSV and per artifact file, plus a Merkle-root
  `artifact.digest` that is the version's immutable content identity.

This service **depends on `just-dna-pipelines`** for `validate_spec`, `compile_module`, and the
`ModuleManifest` models. Reuse that code; do not re-implement compilation or the manifest schema here.

---

## Running the service

- `uv run registry serve` starts the API (Typer CLI → uvicorn). `uv run pytest -q` runs tests.
- The Typer CLI (`src/just_dna_registry/cli.py`) owns admin/ops tasks — `serve`, `init-db`,
  `issue-key`, and future backfill/reindex. Add new ops commands there, not as ad-hoc scripts.
- Deployable as one container + a bucket/HF repo + a DB. No heavyweight orchestration.

When adding a public command, let the **root package's `[project.scripts]`** own it. If `uv run <cmd>`
resolves to a dependency's script after a dependency upgrade, bump this package's version and re-run
`uv sync` so uv rebuilds the `.venv/bin` wrappers — never rename the user-facing command to dodge a
stale wrapper.

---

## Coding Standards

- **Avoid nested try-catch**: try/catch often just hides errors; use it only where an error is an
  unavoidable, handled part of the use case.
- **Type hints**: Mandatory for all Python code.
- **Pathlib**: Always use for file paths.
- **No relative imports**: Always use absolute imports.
- **No inline imports**: All imports at module top level. Never `from X import Y` inside a function or
  method. Only exception: a guarded `try/except ImportError` for optional deps at module level.
- **Polars over Pandas**: Use lazyframes (`scan_parquet`) and streaming (`sink_parquet`) for
  efficiency. Pre-filter dataframes before joining to avoid materialization.
- **Pydantic 2**: Mandatory for data classes — request/response models, config, and the manifest
  contract. FastAPI response models should be explicit Pydantic types, not bare dicts.
- **Typer CLI**: Mandatory for all CLI tools.
- **Logging**: Use the standard-library `logging` system logger. **Eliot is being retired** (see
  `docs/ROADMAP.md` → "Next registry version"): the remaining `eliot` usage (`start_action` in
  `services/publish.py`, the Eliot→stdlib bridge in `logging_setup.py`) is rewired to stdlib
  `logging` next version, and the `eliot` dependency dropped. Do **not** add new Eliot usage; wrap
  multi-step work (publish, compile, backfill) with `logging` at INFO with structured `extra=`.
- **Pay attention to terminal warnings**: Always check output for warnings, especially deprecation
  ones. AI knowledge of APIs can be outdated; these warnings are critical hints to update code.
- **No placeholders**: Never use `/my/custom/path/` or fabricated example values in code.
- **No legacy support**: Refactor aggressively; do not keep old API functions around.
- **Dependency management**: Use `uv sync` and `uv add`. **NEVER** use `uv pip install`.
- **Versions**: Do not hardcode versions in `__init__.py`; read from `pyproject.toml`.
- **Avoid `__all__`**: Avoid `__init__.py` with `__all__` — it obscures where things live.
- **Self-correction**: If an API mistake causes a crash or a real logic failure due to outdated
  knowledge, update this file with the correct API/pattern so future agents don't repeat it.

---

## REST API conventions (see SPEC §8)

- **Base path** `/(...)/api/v1`. All bodies JSON unless noted. Version the API in the path; do not
  break `v1` clients.
- **Pagination**: list endpoints take `?page` and `?per_page` (max 100) and return
  `{items, total, page, per_page}`.
- **Search/facets** on `GET /modules`: `?q=`, `?category=`, `?gene=`, `?genome_build=`, `?owner=`,
  `?license=`, `?sort=downloads|recent|name`. Facet filters (`gene`, `category`) join side tables,
  not full-text.
- **HTTP status contract** (match the spec exactly — clients depend on these):
  - `409 version_exists` — a published `(namespace, name, version)` is **immutable**; re-publish fails.
  - `403 not_namespace_member` — the bearer token's `namespaces` must include the path `{ns}`.
  - `422 invalid_version` / `422` with `errors[]`/`warnings[]` from `ValidationResult` on a bad spec;
    `422 digest_mismatch` when a prebuilt upload disagrees with a sandbox re-compile.
- **Downloads** redirect (`302`) to presigned/CDN URLs; the API serves JSON, not artifact bytes.
- **Auth**: Bearer tokens. Anonymous reads are allowed but throttled harder. Rate-limit with per-token
  buckets (publish 10/h, download 1000/h, search 60/min).
- **Async**: prefer `async def` handlers; never block the event loop with heavy CPU work (compilation,
  hashing) — offload to a thread/executor or a worker.

---

## Manifest & integrity (see SPEC §4–§6)

- The `manifest.json` is the contract and the source of truth. Registry-level fields (`namespace`,
  `version`, `owner`, `license`, `published_at`, `canonical_id`) are filled by **this service** on
  publish; compile-time fields come from `compile_module()`.
- **All hashes are SHA-256, lowercase hex, prefixed `sha256:`.**
  - `inputs[].sha256` — over raw input bytes (no normalization), byte-reproducible by any downloader.
  - `artifact.files[].sha256` — over the concrete written bytes (parquet is **not** deterministic
    across polars/arrow versions, so pin `compiler_version` + `ensembl_reference`).
  - `artifact.digest` — Merkle root: JSON array `[{"name","sha256","size"}, ...]` sorted by `name`,
    serialized with sorted keys and no whitespace, then hashed. This is the version's content identity.
- **`compile_success` is trustworthy only when this server compiled it** (`compiled_by ==
  "marketplace-server"`). Treat foreign `compiled_by` or `false` as untrusted.
- **Immutability + yank**: never mutate a published version's bytes. Yank sets `yanked=true` (drops it
  from default listings and `latest`) but keeps the manifest + artifact fetchable so existing installs
  keep verifying. Un-yank is allowed.
- Prefer **content-addressed storage** (`artifacts/sha256/<digest>/…`) — dedup and immutability for free.

---

## HuggingFace / fsspec access (storage backend)

**Never use `huggingface_hub.snapshot_download`.** It duplicates data into HF's blob store
(`~/.cache/huggingface/`) then copies/links to `local_dir` — wasteful and unreliable. Use **fsspec**
via `HfFileSystem` for direct, file-by-file transfers, which also keeps the backend swappable (S3, GCS,
HTTP) with minimal change:

```python
from huggingface_hub import HfFileSystem, get_token

fs = HfFileSystem(token=get_token())
for remote_path in fs.ls("datasets/org/repo/data", detail=False):
    if remote_path.endswith(".parquet"):
        fs.get(remote_path, str(local_path))
```

Never hardcode HF repo IDs or the Ensembl reference repo in Python — thread them through config
(Pydantic settings / env), mirroring the `modules.yaml` conventions the pipelines use.

---

## Test Generation Guidelines

- **Real data + ground truth**: use actual source data, auto-download if needed, compute expected
  values at runtime rather than hardcoding them.
- **Deterministic coverage**: fixed seeds or explicit filters; representative *and* edge cases.
- **Meaningful assertions**: prefer relationships and aggregates over existence-only checks; prefer
  set equality (`assert set_a == set_b`) over count checks.
- **Verbosity**: run `pytest -vvv`. Keep `pytest` in the workspace/dev dependencies.
- **Docs**: put new markdown (except `README`/`CLAUDE`) in `docs/`.

**Service-specific tests to write** (SPEC §13):
- Contract test per endpoint. `finalize` with an invalid spec → `422` carrying
  `ValidationResult.errors`; re-publishing an existing version → `409`.
- Integrity round-trip: publish → tamper one artifact byte → client verification detects the
  `artifact.digest` mismatch.
- Manifest correctness: `inputs[].sha256` equals `hashlib.sha256` of the source CSVs; non-empty
  `artifact.files[]`; `compile_success == true`; `stats.genes`/`categories` match a fixture.

**Avoid** these AI-generated anti-patterns: happy-path-only tests, hardcoded counts derived from data
inspection (`assert len(x) == 270`), mocking data transformations instead of running the real path,
and claiming a test "would have caught" a bug without demonstrating the failure on the buggy code
first. Hardcoding well-known **domain constants** (enum values from a spec) is fine; hardcoding
row/unique counts derived from inspecting data is not.

---

## Documentation & prose style

- Write in natural, human prose. Avoid AI-typical patterns (em-dash pile-ups, filler transitions,
  marketing voice). Never hallucinate documentation or overpromise unimplemented features.
- Keep READMEs concise; move deep implementation detail to `docs/`.
- When describing the platform, frame it as a bioinformatics tool that *joins* VCF data against module
  databases to add annotations. Never imply the VCF already contains annotations, and never claim the
  tool makes gene–disease inferences.
- Update `CLAUDE.md` and any affected `docs/` immediately whenever code is refactored.

---

## Related repos

Part of a multi-root ecosystem: `just-dna-lite` (main app + webui), `just-dna-pipelines`
(compiler/discovery — this service's dependency), `just-prs`, `prepare-annotations`, `dna-seq`.
Treat sibling repos as **read-only** unless the task explicitly targets them. This registry plugs
into the existing `Source` discovery model as *just another source* (`registry://`), so existing
HuggingFace/local modules keep working with zero migration.
