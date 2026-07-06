# just-dna-marketplace

A catalog / publish / download **REST API** for [just-dna-lite](../just-dna-lite) annotation
modules. Authors publish module specs; the server validates, recompiles, stores, and indexes them;
consumers browse, search, download, and integrity-verify. There is no frontend here — the webui and
Dagster pipelines are consumers of this API.

**Live:** <https://module-marketplace.just-dna.life> · health `GET /health` · API under `/api/v1`
· interactive docs at [`/docs`](https://module-marketplace.just-dna.life/docs).

See [docs/SPEC.md](docs/SPEC.md) for the full design and [docs/ROADMAP.md](docs/ROADMAP.md) for
build status.

## Install

Two shapes from one package:

```bash
pip install just-dna-marketplace            # client only (lightweight): httpx + just-dna-format
pip install just-dna-marketplace[server]    # + FastAPI app, server-side recompile, storage, admin
```

The default install is the **reference client** — import it instead of re-implementing the REST
calls + integrity verification:

```python
from just_dna_marketplace import MarketplaceClient

with MarketplaceClient("https://module-marketplace.just-dna.life", token="mk_live_…") as mkt:
    print(mkt.list_modules())
    mkt.import_module("just-dna-seq", "coronary", "1.0.0", "coronary_v1.zip")   # publish a zip
    mkt.download("just-dna-seq", "coronary", "1.0.0", "./coronary")             # fetch + verify
    mkt.lookup_by_digest("sha256:…")                                            # already published?
```

Or the `marketplace-client` CLI (ships with the client install):

```bash
export MARKETPLACE_URL=https://module-marketplace.just-dna.life MARKETPLACE_TOKEN=mk_live_…
marketplace-client list
marketplace-client download just-dna-seq coronary 1.0.0 ./coronary
```

## Run the server (needs `[server]`)

```bash
uv sync                      # dev env (includes the server extra + tests)
uv run pytest -q
uv run marketplace issue-key <account> -n <namespace>   # mint an API key
uv run marketplace serve --host 0.0.0.0 --port 8000     # /docs for the interactive API
```

## What works today

- **Read/catalog API** — list + search (`?q`, `?gene`, `?category`, `?genome_build`, `?owner`,
  `?license`, `?sort`), module detail, versions, manifest (SPEC §8.1–§8.4).
- **Publish** — multipart spec upload **or** zip/tar.gz archive import (incl. legacy parquet-only
  via reverse-engineering), server-side recompiled so `compile_success`/digest are trusted.
- **Download + integrity** — per-file + streamable tar.gz, verify-then-install via
  `just_dna_format.verify_manifest` (SPEC §5).
- **Logs** over the API; **digest lookup**; **auth** (static API keys) + namespace ownership;
  **yank / un-yank**; ops-only **hard removal** (`marketplace remove-namespace/-module`).

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
