# just-dna-registry — Roadmap

Priority-ordered plan for building the annotation module registry, MVP-first. The full
contract lives in [SPEC.md](SPEC.md); this doc is the *build order*. Section references (§) point
into the spec.

## Guiding decisions

| Area | Decision |
|---|---|
| Manifest contract | The **`just-dna-format`** workspace (repo `dna-seq/just-dna-format`) holds two packages: **`just-dna-format`** (pure Pydantic schema/contract — DSL spec + manifest + integrity + identity) and **`just-dna-compiler`** (the transform, + polars/duckdb). Single source of truth for `just-dna-pipelines` and this service; prevents drift, and keeps Dagster/LLM deps out of both. |
| Artifact storage | **HuggingFace Hub** datasets via `HfFileSystem` (never `snapshot_download`). |
| Publish trust model | **Server-side recompile** — publisher uploads the spec only; the server runs `compile_module()` and produces the trusted manifest/digests (§7). |
| Auth | **Static API keys**; one account owns one namespace. JWTs, orgs, and membership are deferred. |

## Starting state (why M0/M1 come first)

The SPEC §10 prerequisite work does **not** exist yet in `just-dna-pipelines` (v0.1.6, a local
workspace member, not on PyPI):

- No `ModuleManifest` contract; `compile_module()` writes 3 parquets but **no `manifest.json`, no
  SHA-256 hashing, no artifact digest**.
- `validate_spec` stats expose `categories` as a list but `genes` only as a count.
- `_SPEC_SUFFIXES` is function-local and excludes `.json`.

So the integrity contract has to be built (M0) and wired into compilation (M1) before the service
can trust or emit manifests.

## MVP — the finish line

An authenticated author uploads a **spec** → the server **validates + recompiles** it against a
pinned Ensembl reference → writes the module dir + `manifest.json` to an **HF dataset repo** →
**indexes** it. Anyone can then **browse / search / filter / inspect versions**, **download** the
artifact, and **verify its integrity**. Authors can **yank**. Everything outside that loop is
deferred.

---

## Milestones

| # | Milestone | Scope | Status |
|---|---|---|---|
| **M0** | `just-dna-format` shared contract | Schema (DSL spec + manifest + integrity + identity) + reference compiler, as a 2-package uv workspace (§4–§6). **Blocks all publish/verify.** | ✅ **done** — 44 tests |
| **M1** | `just-dna-pipelines` integration | Repoint pipelines' `module_compiler` to `just-dna-compiler` (re-export shim), delete the duplicate, add `.json` on install (§10 A4). Cross-repo. | ⏳ **deferred** (cross-repo) |
| **M2** | Service skeleton | Config, DB projection schema, storage backend interface, FastAPI app, `/health`, admin CLI. | ✅ **done** |
| **M3** | Read / catalog API | Endpoints 1–4: list/search/facets, detail, versions, manifest (§8.1–§8.4). No auth. | ✅ **done** |
| **M4** | Publish pipeline | API-key auth, multipart spec upload → server-side recompile (`just-dna-compiler`) → store → index (§8.6–§8.7). | ✅ **done** (local backend; HF commit pending) |
| **M5** | Download + integrity | Download/files redirects + reference client verify-then-install (§5, §8.5). | ✅ **done** (local backend; HF redirect pending) |
| **M6** | Yank + finish | Yank/un-yank, `whoami`, basic rate limiting (§6, §7, §8.8). | 🚧 **yank + whoami done; rate limiting pending** |

**Dependency order:** M0 → M1 → M2 → M3 / M4 (M4 needs M0+M1+M2) → M5 → M6.

## 0.3 — community-first onboarding (shipped)

Goal: publishing from the just-dna-lite UI **without leaving the app**, community-foster over
security. Bootstrapping a publisher is now self-service, gated by a lightweight anti-spambot
proof-of-work rather than admin issuance.

- ✅ **Install-id (proof-of-work).** The lite app mints an install-id once at first run:
  `jdi1_<random>_<nonce>` whose SHA-256 has ≥ `install_id_difficulty` (default 20) leading zero
  bits (`installid.generate_install_id` / `validate_install_id`, shared in the base package).
  Open-source ⇒ not malpractice-resistant, but deters random/bulk AI-spambot ids; verify is O(1).
- ✅ **Self-registration.** `POST /api/v1/auth/register {install_id, account}` validates the PoW and
  mints an account + API key (one account per install-id; re-register re-issues a key). Gated by
  `allow_self_register` (default on).
- ✅ **Namespace claim, tied to install-id.** `GET /api/v1/namespaces/{ns}` (availability) +
  `POST /api/v1/namespaces {namespace}` (claim). Each account may hold up to
  `namespaces_per_account` (default **5**); over that → `403 namespace_limit_reached`, taken →
  `409 namespace_taken`.
- ✅ **Provenance without a spec change.** Downloaded vs custom is read from `manifest.json`
  (`compilation.compiled_by == "marketplace-server"` + `identity`); the client **stamps** the
  published manifest back into the local spec dir so "published-by-me" is self-marked. Batch
  `POST /api/v1/modules/lookup {digests:[...]}` classifies many local modules in one request
  (digests are already in each manifest — no client hashing, one indexed query). **The DSL
  `module_spec.yaml` is deliberately not changed** — provenance lives in the manifest (its layer).
- ✅ **Client + CLI:** `RegistryClient.register/namespace_available/claim_namespace/lookup_by_digests`;
  `registry-client register|namespace-available|claim-namespace`; `generate_install_id` exported.

DB: `accounts.install_id` (unique, nullable — admin keys exempt) added via an idempotent migration
so the live catalog upgrades in place. Still deferred: expiring JWTs / OAuth, org membership, and
true abuse-resistance (the PoW is a deterrent, not a wall).

## 0.4 — moderation, ops, HF storage, webui deliverable

Scope: everything outstanding **except** S3/MinIO (overkill for now) and Postgres (→ 0.5). A5
backfill dropped (existing annotators are processed elsewhere).

**Shipped**
- ✅ **Featured namespaces** — `featured` flag; float to the top of every listing; `?featured=true`
  restricts. Card carries `featured`. Admin CLI `feature`/`unfeature`.
- ✅ **Blacklisted namespaces** — hidden from default `GET /modules` + search; reachable via
  `?namespace=`, `?include_blacklisted=true`, or direct detail. Admin CLI `blacklist`/`unblacklist`.
- ✅ **Key revocation** — `registry revoke-key` / `revoke-account` (closes the leaked-key gap).
- ✅ **Rate limiting** (SPEC §7) — in-memory token buckets per caller × category on
  search/download/publish; `429 rate_limited`; configurable, on by default.
- ✅ **`HfStorage` backend** — HF dataset repo under `data/{ns}/{name}/{version}/…`; writes via a
  single commit, reads via `HfFileSystem`, `file_url` → HF `resolve` CDN URL so downloads `302`.
  Selected by `storage_backend=hf`; the startup token guard (0.2.1) gates it. *Live commit/read is
  integration-tested with a real token + public repo (offline unit tests cover paths/URLs).*
- ✅ **webui registry-page deliverable** — [WEBUI-STORE.md](WEBUI-STORE.md): the
  client + response shapes + provenance/onboarding wiring the webui builds its catalog page on.

- ✅ **Optional JWT sessions** — `POST /auth/tokens` exchanges a static API key for a short-lived
  JWT that's also accepted as a bearer. Off unless `jwt_secret` is set; static keys always work
  (backwards-compatible, 0.4 behaviour unchanged).

**Dropped (planning legacy).** The **prebuilt-parquet upload / "trust-but-verify"** mode is gone:
it only existed so the registry could avoid bundling the compiler — but we recompile server-side
now, so there's no prebuilt artifact to ingest. (If we ever need to *check reproducibility* of two
compiles, compare parquet **frame-shape + canonically-sorted content** rather than byte digests —
parquet isn't byte-deterministic across arrow versions. Not needed today.)

**Deferred within 0.4 (rationale)** — not blockers; each needs more than a quick pass:
- **Ed25519 signing** — would put a crypto dep in the *light* client (for verify) and needs
  server keypair ops; SPEC marks it "Future". Revisit deliberately.
- **Presigned PUT upload** — mainly matters once large-parquet HF uploads are the norm; multipart is
  fine now. Pairs with hardening `HfStorage`.
- **OAuth + org membership** — needs a provider/product decision; install-id self-register + optional
  JWT cover the community MVP.
- **Download analytics** — beyond the counter; low priority.

**→ 0.5:** Postgres migration; FTS5 / advanced search (grouped as the search-at-scale effort).
**Excluded:** S3/MinIO.

### Current state (2026-07-07) — v0.4.0, live

**Live** at <https://module-registry.just-dna.life>. Depends on the published PyPI packages
`just-dna-format>=0.1.0` + `just-dna-compiler>=0.1.0`. **39 tests green**; full integration run
passed against the live server. Packaged **client-first**: default install is the reference client
(`from just_dna_registry import RegistryClient`); the server is the `[server]` extra.

Shipped (beyond the core M2–M5 loop):

- ✅ **Publish** — multipart spec upload **and** zip/tar.gz **archive import** (spec archive or
  legacy parquet-only via `reverse_module`), server-side recompiled.
- ✅ **Download** — per-file verify-then-install **and** streamable **tar.gz** (`?format=tarball`);
  generalized `…/files/{path}` serves parquets, logs, and inputs.
- ✅ **Logs over the API** (`…/versions/{v}/logs` + file serving).
- ✅ **Digest lookup** (`GET /modules/lookup?digest=`).
- ✅ **Auth** (static keys) + ownership, **whoami**, **yank/un-yank**.
- ✅ **Ops-only hard removal** (`registry remove-module` / `remove-namespace`).
- ✅ **Debug logging** behind `REGISTRY_DEBUG` (request tracing + Eliot pipeline steps).
- ✅ **HF token startup guard** — `storage_backend=hf` validates a write-capable token or exits 1.
- ✅ **Reference client + CLI** (`registry-client`); docs: `API-REFERENCE.md`, `CLIENT.md`,
  `CHANGELOG.md`, `.env.template`.

What remains for a full MVP:

- **M1** (in `just-dna-lite/just-dna-pipelines`): repoint its `module_compiler` to
  `just-dna-compiler` (re-export shim + delete the duplicate) and add `.json` to `_SPEC_SUFFIXES`
  (§10 A4). Cross-repo, deferred pending the go-ahead to edit it.
- **`HfStorage`** backend (currently only `LocalStorage`) for real `302` CDN redirects + HF commit.
  (The startup token guard is already in place for when it lands.)
- **Rate limiting** (M6); cross-version provenance aggregation.
- **Ensembl at publish** is opt-in: `resolve_with_ensembl` defaults **off** (specs must carry
  positions); enable with a reference cache via `JUST_DNA_PIPELINES_CACHE_DIR` /
  `REGISTRY_ENSEMBL_CACHE`.

Run it: `uv run registry serve` · issue a key: `uv run registry issue-key <acct> -n <ns>`.

### M0 — `just-dna-format` shared contract package

Minimal package, Python ≥3.13, deps: `pydantic` + stdlib only.

- **Manifest models (§4):** `ModuleManifest` with `Identity`, `Display`, `Stats`, `Compilation`,
  `InputFile`, `Artifact`/`ArtifactFile`. Registry-only fields (namespace, version, owner,
  license, published_at, canonical_id) are `Optional`, filled on publish.
- **Integrity helpers (§5):** `sha256_file() -> "sha256:…"`; `artifact_digest(files)` (canonical
  Merkle root — JSON array of `{name,sha256,size}` sorted by name, `sort_keys`, no whitespace, then
  hash); `build_manifest(...)`; client `verify_manifest(dir, manifest)` (per-file hash → recompute
  digest → `compile_success` + `compiled_by == "marketplace-server"`).
- **Identity/versioning (§6):** name regex `^[a-z][a-z0-9_]*$`, `canonical_id`, SemVer
  parse/compare, `vN → N.0.0` legacy mapping.
- **Tests:** digest order-independent and stable; tamper-one-byte → verify fails; fixture round-trip.

### M1 — `just-dna-pipelines` integration (upstream, additive)

- **A2:** `compile_module()` writes `manifest.json` using the shared models — input hashes over raw
  spec bytes, artifact file hashes/sizes over written parquets, artifact digest.
- **A3:** expose `genes: list[str]` in `validate_spec` stats (filter `None`); normalize
  `variant_count` / `study_count` / `gene_count`. (`categories` is already a list.)
- **A4:** promote `_SPEC_SUFFIXES` to a module-level constant and add `.json` so `manifest.json`
  survives `register_custom_module`.

### M2 — registry service skeleton (this repo)

- **Deps** (`uv add`): `just-dna-format`, `just-dna-pipelines` (path/workspace), a DB layer (SQLite),
  `huggingface-hub`, `eliot`, `python-dotenv`. Drop `polars-bio` from direct deps unless the API
  reads parquet directly (stats come from the manifest).
- **Config** (Pydantic settings): HF dataset repo id + write token, pinned Ensembl reference, SQLite
  path, API-key store. Load `.env` before reading env.
- **DB schema (§9)** — a *projection* of `manifest.json`: `accounts`/`api_keys`, `namespaces`,
  `modules(namespace, name, …)`, `versions(module_id, version, digest, manifest_json,
  compile_success, yanked, created_at, downloads)`, and `version_genes` / `version_categories` facet
  tables.
- FastAPI app factory + `/health`, Eliot logging, Typer admin CLI stub.

Proposed layout: `src/just_dna_registry/{config,cli}.py`, `db/`, `storage/` (abstract
`StorageBackend` + `HfStorage`), `api/{app,deps}.py`, `api/routers/`, `services/`.

### M3 — read / catalog API (no auth)

Endpoints 1–4 (§8.1–§8.4): `GET /api/v1/modules` (search `?q`/`?category`/`?gene`/`?genome_build`/
`?owner`/`?license`/`?sort`, pagination `{items,total,page,per_page}`, `per_page` max 100),
`GET /modules/{ns}/{name}` (detail + readme + versions + `latest_manifest`), `.../versions`,
`.../versions/{v}/manifest`. Search = title/description match + facet joins. Explicit Pydantic
response models.

### M4 — publish pipeline (server-side recompile + API-key auth)

- **Auth:** static API-key bearer → account + owned namespaces; `{ns}` must be owned else
  `403 not_namespace_member`.
- **Init** `POST /modules/{ns}/{name}/versions`: declare version + files with client hashes;
  `409 version_exists`, `422 invalid_version`. **MVP upload = `multipart/form-data`** of the spec
  (SPEC-sanctioned MVP alternative to presigned PUT).
- **Finalize** `POST .../versions/{v}/finalize`: verify hashes → `validate_spec()` (`422` with
  `errors`/`warnings`) → `compile_module()` with the **pinned Ensembl reference** → `build_manifest()`
  (`compile_success`, `compiled_by="marketplace-server"`) → commit module dir (`data/{name}/v{N}/`
  incl. `manifest.json`) to the **HF dataset repo** → upsert DB + facet tables. Response `201` with
  the full manifest.
- **Ensembl provisioning:** download/cache the reference on first compile (fsspec, not
  `snapshot_download`). Compilation is CPU-heavy — run it off the event loop (executor/thread);
  a dedicated worker/subprocess is an optimization, not required for MVP.

### M5 — download + integrity

`GET .../versions/{v}/download` → `302` to the HF `resolve` URL for the module tarball;
`?format=files` → per-file `{name,url,sha256,size}`. `GET .../versions/{v}/files/{file}` → redirect
to the HF file URL. Increment the `downloads` counter. Ship a reference client verify-then-install
built on `just_dna_format.verify_manifest` (also the seed for the future webui `registry://`
source).

### M6 — yank + finish

`POST .../versions/{v}/yank` (+ un-yank): set `yanked=true`, drop from default listings and `latest`,
keep manifest/artifact fetchable. `GET /auth/whoami` (identity + namespaces). Basic per-key rate
limiting (publish/download/search buckets, §7) — a simple in-memory token bucket for MVP.

---

## Deferred — nice-to-haves (post-MVP)

- **Presigned PUT upload flow** (init → targets → finalize) for large parquet. MVP uses multipart.
- **Full auth:** `POST /auth/tokens` issuing expiring JWTs; **org namespaces + member management**.
- **Prebuilt "trust-but-verify" publish mode** (sandbox recompile + digest compare,
  `422 digest_mismatch`).
- **A5 backfill** of existing `just-dna-seq/annotators` into manifests + index (ops/CLI task).
- **Ed25519 signing** of `artifact.digest` + published pubkey (§5 "Future").
- **Postgres** migration; **FTS5** / advanced search; download analytics.
- **S3/MinIO** storage backend behind the same `StorageBackend` interface.
- **webui / Dagster consumer integration** (§11 — `registry://` source branch, catalog page) —
  lives in `just-dna-lite`, not this repo.

### Namespace curation & moderation

- **Featured namespaces** — an admin-set `featured` flag (on `namespaces`, or a `namespace_flags`
  table) so the catalog can surface a curated front page / `?featured=true` filter / a "featured
  first" sort. Purely additive to the projection.
- **Blacklisted namespaces (hidden by default)** — a moderation flag that removes a namespace's
  modules from default `GET /modules` listings and search; they are returned **only when directly
  requested** (e.g. explicit `GET /modules/{ns}/{name}` or an opt-in `?include_blacklisted=true` /
  `?namespace=<ns>`). Distinct from yank (which is per-version); this hides an entire namespace
  without deleting it. For spam/abuse.
- **Server-side hard removal (ops-only, not the API, not yank)** — ✅ **done**: admin CLI
  `registry remove-module <ns> <name>` and `remove-namespace <ns>` purge DB rows (versions +
  `version_genes`/`version_categories` cascade, modules, namespace ownership) **and** the stored
  artifacts (`storage.remove`), so the namespace is fully reclaimable — a new key re-submits with
  old versions gone. Off the public API (ops/console only), `--yes` to skip the confirm.

---

## 0.7 — format 0.3 adoption + upgrade automation (shipped)

- **Adopted `just-dna-format` / `just-dna-compiler` 0.3.** Pins bumped to `>=0.3.0`. The new columns
  (`direction`, `stat_significance`, `effect_size`/`effect_measure`, `flags`, `effect_allele`,
  `trait_efo_id`, `clin_sig`) flow through publish/recompile automatically — they are additive and
  the server recompiles every spec, so a published module simply gains them on its next publish.
- **`revalidate` now surfaces 0.3 drift as `upgradable`.** Because the 0.3 columns are additive, a
  legacy module still *validates*; the audit distinguishes `ok` / **`upgradable`** (validates, but
  the additive axes can be back-populated from the legacy `state`/booleans) / `needs_upgrade` (fails
  the current validator) / `skipped`. `--set-flag` marks both `upgradable` and `needs_upgrade`.
- **New `registry upgrade` command + `services/upgrade.py`.** Consumes the format's own
  `VariantRow.upgraded()` derivation to migrate a version's `variants.csv` (back-populate
  `direction`/`stat_significance`/`clin_sig` + trim `state`) and re-publish as the next PATCH through
  the normal server-side compile path. Dry-run by default; `--apply` publishes. The predecessor is
  never mutated (immutability) and the transform is idempotent. This is the automation of
  `docs/UPGRADE.md` step 3 for the 0.3 additive-column contract.
- The diplotype/copy-number *shapes* (format items 7/7b) stay representation-only until a consumer
  (just-dna-lite) can call them; nothing registry-side is needed for them yet.

### 0.7.1 — server/client version-mismatch guard

Bumping the format contract to 0.3 surfaced a real collision: a client on one `just-dna-format`
contract talking to a server on another produces a cryptic digest / catalog-shape error. 0.7.1 adds
an explicit guard:

- The server advertises its versions — `GET /api/v1/version` (`{api, registry, format, compiler}`)
  plus `X-Registry-Version` / `X-Format-Version` / `X-API-Version` response headers on **every**
  response — and the client sends its own versions as request headers.
- Before publish/import/download, the client calls `assert_compatible()`: it fetches the server's
  versions and raises `VersionMismatchError` (HTTP 409) with an actionable message when the API
  version or the `just-dna-format` contract can't interoperate. Contract rule: same MAJOR, and while
  `0.x` also the same MINOR (a 0.x minor moves the parquet schema / `artifact.digest` — exactly the
  0.2→0.3 case). A differing registry *app* version is **not** fatal (the API is path-versioned).
- A pre-0.7.1 server (no `/version`) can't be checked, so the guard warns and proceeds.
  `REGISTRY_SKIP_VERSION_CHECK=1` (or `RegistryClient(check_version=False)`) is the escape
  hatch; `registry-client version` prints both sides and the verdict. Logic in `version.py`.

## Next registry version (post-0.7)

- **Retire Eliot → stdlib `logging`.** Rewire the Eliot usage — `start_action` in
  `services/publish.py` and the Eliot→stdlib bridge in `logging_setup.py` — onto the standard-library
  `logging` system logger, and drop the `eliot` dependency from `pyproject.toml`. The
  `just-dna-format` packages already use only stdlib `logging`; this aligns the registry with them
  and with the CLAUDE.md logging standard. (Was bundled with the format-0.3 adoption; that shipped in
  0.7 without touching logging, so this is now a standalone task.)

---

## Verification

- **M0:** `pytest -vvv` on `just-dna-format` — digest order-independence/stability,
  tamper → verify-fail, fixture round-trip (§13).
- **M1:** unit-test `compile_module` emits `manifest.json` with `inputs[].sha256` matching
  `hashlib.sha256` of the source CSVs, non-empty `artifact.files[]`, `compile_success=true`,
  `stats.genes`/`categories` matching a fixture.
- **M3–M6:** FastAPI `TestClient` contract tests per endpoint — invalid spec on finalize → `422`
  with `ValidationResult.errors`; re-publish existing version → `409`; publish under an unowned
  namespace → `403`.
- **End-to-end (MVP done):** publish a fixture spec → appears in `GET /modules` with correct stats →
  download in a second client → `verify_manifest` passes → tamper one byte → verification detects the
  digest mismatch. Run the API with `uv run uvicorn just_dna_registry.api.app:app`.
