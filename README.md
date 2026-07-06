# just-dna-marketplace

A catalog / publish / download **REST API** for [just-dna-lite](../just-dna-lite) annotation
modules. Authors publish module specs; the server validates, recompiles, stores, and indexes them;
consumers browse, search, download, and integrity-verify. There is no frontend here — the webui and
Dagster pipelines are consumers of this API.

**Live:** <https://module-marketplace.just-dna.life> · health `GET /health` · API under `/api/v1`
· interactive docs at [`/docs`](https://module-marketplace.just-dna.life/docs).

See [docs/SPEC.md](docs/SPEC.md) for the full design and [docs/ROADMAP.md](docs/ROADMAP.md) for
build status.

## Quick start

```bash
uv sync
uv run pytest -q                       # run the test suite
uv run marketplace serve               # start the API on http://127.0.0.1:8000
```

Then open `http://127.0.0.1:8000/docs` for the interactive API, or:

```bash
curl http://127.0.0.1:8000/api/v1/modules          # list/search the catalog
uv run marketplace issue-key antonkulaga -n just-dna-seq   # mint an API key for publishing
```

## What works today

- **Read/catalog API** — list + search (`?q`, `?gene`, `?category`, `?genome_build`, `?owner`,
  `?license`, `?sort`), module detail, versions, manifest (SPEC §8.1–§8.4).
- **Download + integrity** — per-file descriptors with SHA-256, byte serving, and a
  verify-then-install round-trip via `just_dna_format.verify_manifest` (SPEC §5).
- **Auth** — static API keys; `whoami`; namespace ownership gate on publish (SPEC §8.8).
- **Yank / un-yank** — unlist a version while keeping its artifact fetchable (SPEC §6).

Publishing runs all its guards but stops at the server-side recompile step (`501`) until the
`just-dna-pipelines` integration and HuggingFace storage land — see the roadmap.

## Architecture

The `manifest.json` of each version is the **source of truth**; the SQLite catalog is a rebuildable
projection of it. The manifest contract and integrity primitives live in the shared, dependency-light
[`just-dna-format`](../just-dna-format) package so this service and the compiler never drift.

```
src/just_dna_marketplace/
  config.py            # Pydantic settings
  db/                  # SQLite schema + repository (the projection)
  storage/             # StorageBackend interface + LocalStorage (HfStorage pending)
  models/api.py        # card / detail / version / page response models
  services/            # catalog (reads) + ingest (manifest -> projection)
  api/                 # FastAPI app, deps (auth/pagination), routers
  cli.py               # `marketplace` admin CLI
```
