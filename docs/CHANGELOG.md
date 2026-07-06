# Changelog

All notable changes to **just-dna-marketplace**. Format follows
[Keep a Changelog](https://keepachangelog.com/); versions are [SemVer](https://semver.org/).

Full API: [API-REFERENCE.md](API-REFERENCE.md) · client: [CLIENT.md](CLIENT.md) · plan:
[ROADMAP.md](ROADMAP.md).

## [0.2.1] — 2026-07-07

### Added
- **HF token startup guard** — when `storage_backend=hf`, the server validates on startup that the
  configured HF token is present, valid, and has **write** access to the dataset repo, and exits
  with code `1` otherwise. No-op for the local backend.
- Docs: `API-REFERENCE.md` (exhaustive REST reference), `CLIENT.md` (Python + CLI surface),
  `CHANGELOG.md`.

## [0.2.0] — 2026-07-07

Client-first packaging + a live deployment at <https://module-marketplace.just-dna.life>.

### Changed
- **Client-first library layout.** The default install is now the reference **client** only
  (deps: `httpx`, `typer`, `python-dotenv`, `just-dna-format`); `from just_dna_marketplace import
  MarketplaceClient`. The server (FastAPI app, `just-dna-compiler`, storage, admin CLI) moved to
  the **`server` optional extra** — `pip install just-dna-marketplace[server]`.
- Depends on the published PyPI packages `just-dna-format>=0.1.0` + `just-dna-compiler>=0.1.0`
  (no local path sources).

### Fixed
- `GET /modules/{ns}/{name}` (detail) now returns the **full** `stats.genes` list (SPEC §8.3);
  only list/search cards truncate to the top 3.

## [0.1.0] — 2026-07-06

Initial marketplace service (internal builds; superseded by 0.2.0 packaging).

### Added
- **Read / catalog API** — `GET /modules` (search `q`, facet filters `category`/`gene`/
  `genome_build`/`owner`/`license`, `sort=name|downloads|recent`, pagination), module detail,
  version list, full manifest.
- **Publish (server-side recompile)** — `POST …/versions` takes a multipart **spec** upload; the
  server runs `validate_spec` + `compile_module(compiled_by="marketplace-server")`, fills the
  marketplace manifest fields, stores the version, and indexes it. Guards: `401` auth, `403`
  namespace ownership, `422 invalid_version`, `409 version_exists`, `422 {invalid_spec|compile_failed|
  name_mismatch}`.
- **Archive import** — `POST …/versions/import` accepts a **zip/tar.gz**: a spec archive is
  recompiled directly; a legacy parquet-only archive is reverse-engineered (`reverse_module`, with
  client-supplied display metadata) then recompiled. Path-traversal-safe extraction.
- **Download + integrity** — `…/versions/{v}/download?format=files` (per-file `{name,url,sha256,
  size}`) and `?format=tarball` (streamable `tar.gz` of the whole version); `…/files/{path}` serves
  any manifest-listed file (parquet, log, input) or `302`-redirects. Verify-then-install via
  `just_dna_format.verify_manifest`.
- **Provenance logs over the API** — `…/versions/{v}/logs` lists per-version run logs (top-level
  `*.log` + a `logs/` per-role subtree), fetched through the files endpoint.
- **Digest lookup** — `GET /modules/lookup?digest=` returns published versions matching an
  `artifact.digest` (dedup / "already published?").
- **Auth** — static API-key bearer; `GET /auth/whoami`; namespace ownership gate on writes.
- **Yank / un-yank** — `POST …/versions/{v}/yank`; drops from default listings + `latest`, keeps
  the artifact fetchable.
- **Version-scoped storage** (`{ns}/{name}/{version}`) behind a `StorageBackend` interface;
  `LocalStorage` shipped (`HfStorage` pending). `artifact.digest` remains the content identity.
- **Debug logging** behind `MARKETPLACE_DEBUG` — request tracing, always-on exception tracebacks,
  and Eliot-structured publish/import step logs (one `task_uuid` per request).
- **Reference client** (`MarketplaceClient`) + **`marketplace-client` CLI** (list, download
  [+`--tarball`], publish, import-module, find-by-hash, update-module-version).
- **Admin CLI** (`marketplace`) — `serve`, `init-db`, `issue-key`, and ops-only hard removal
  `remove-module` / `remove-namespace` (purges DB rows + artifacts, frees the namespace; not yank).
- `.env.template`, `docs/SPEC.md`, `docs/ROADMAP.md`.

[0.2.1]: #021--2026-07-07
[0.2.0]: #020--2026-07-07
[0.1.0]: #010--2026-07-06
