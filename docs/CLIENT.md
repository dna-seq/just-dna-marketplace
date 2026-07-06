# just-dna-marketplace — Client Reference

The reference client is the **default install** of `just-dna-marketplace` — import it instead of
re-implementing REST calls + integrity verification. It ships as a Python library
(`MarketplaceClient`) and an equivalent CLI (`marketplace-client`). Wire protocol:
[API-REFERENCE.md](API-REFERENCE.md).

## Install

```bash
pip install just-dna-marketplace            # client only (httpx + just-dna-format)
pip install just-dna-marketplace[server]    # + the server (FastAPI app, compiler, admin CLI)
```

## Configuration

Both the Python client and the CLI take the base URL and (for writes) an API key. The CLI reads
them from flags, then environment, then a local `.env`:

| Setting | Env var | CLI flag | Default |
|---|---|---|---|
| Base URL | `MARKETPLACE_URL` | `--url` | `http://127.0.0.1:8000` |
| API key | `MARKETPLACE_TOKEN` | `--token` | — (required for publish/import/update) |

```bash
export MARKETPLACE_URL=https://module-marketplace.just-dna.life
export MARKETPLACE_TOKEN=mk_live_…
```

## Python ↔ CLI at a glance

| Capability | `MarketplaceClient` method | `marketplace-client` command | Auth |
|---|---|---|---|
| List / search | `list_modules(**filters)` | `list` | — |
| Module detail | `get_module(ns, name)` | *(use the API / `download`)* | — |
| Version list | `versions(ns, name)` | *(via detail)* | — |
| Full manifest | `manifest(ns, name, v)` | *(written by `download`)* | — |
| List logs | `logs(ns, name, v)` | *(via `download`, which fetches them)* | — |
| Find by digest | `lookup_by_digest(digest)` | `find-by-hash` | — |
| Batch find by digest | `lookup_by_digests(digests)` | *(programmatic)* | — |
| Self-register | `register(install_id, account)` | `register` | install-id |
| Namespace availability | `namespace_available(ns)` | `namespace-available` | — |
| Claim namespace | `claim_namespace(ns)` | `claim-namespace` | bearer |
| Download + verify | `download(ns, name, v, dest)` | `download` | — |
| Download tarball | `get_tarball(ns, name, v, dest)` | `download … --tarball` | — |
| Publish (spec dir) | `publish(ns, name, v, spec_dir)` | `publish` | bearer |
| Publish (archive) | `import_module(ns, name, v, archive)` | `import-module` | bearer |
| Bump a version | *(`get_module` + `publish`)* | `update-module-version` | bearer |

---

## Python: `MarketplaceClient`

```python
from just_dna_marketplace import MarketplaceClient, MarketplaceError

with MarketplaceClient("https://module-marketplace.just-dna.life", token="mk_live_…") as mkt:
    ...
```

**`MarketplaceClient(base_url, token=None, timeout=120.0, transport=None)`** — a context manager
(closes the underlying `httpx.Client`). `transport` is for tests (e.g. an ASGI transport).
Non-2xx responses raise **`MarketplaceError(status_code, detail)`**.

### Reads (no token)

- **`list_modules(**filters) -> dict`** — a `Page` of cards. Filters: `q, category, gene,
  genome_build, owner, license, sort, page, per_page` (Nones dropped).
- **`get_module(namespace, name) -> dict`** — module detail (readme, versions, `latest_manifest`,
  full `stats.genes`).
- **`versions(namespace, name) -> dict`** — paginated `VersionSummary` list.
- **`manifest(namespace, name, version) -> ModuleManifest`** — the parsed `just_dna_format`
  manifest.
- **`logs(namespace, name, version) -> list[dict]`** — `[{name, sha256, size, url}]`.
- **`lookup_by_digest(digest) -> list[dict]`** — matches `[{namespace, name, version, yanked}]`
  (empty if none).
- **`lookup_by_digests(digests) -> dict[str, list[dict]]`** — batch: `{digest: matches}` in one
  request (classify many local modules; digests come from their `manifest.json`).

### Onboarding (community self-service)

- **`register(install_id, account) -> dict`** — `{token, account, namespaces}` from a proof-of-work
  install-id (no auth; mints the key). Grind an id with `generate_install_id()` (also exported at
  top level: `from just_dna_marketplace import generate_install_id`).
- **`namespace_available(namespace) -> dict`** — `{namespace, valid, available}`.
- **`claim_namespace(namespace) -> dict`** *(token)* — claims it for your account
  (`{namespace, owner, already_owned}`); raises `MarketplaceError` `409`/`403` if taken/over-limit.
- **`download(namespace, name, version, dest, *, include_logs=True) -> ModuleManifest`** — fetches
  the artifact files (and logs), writes `manifest.json`, and **verifies integrity** with
  `verify_manifest` (raises `IntegrityError` on mismatch). Returns the manifest.
- **`get_tarball(namespace, name, version, dest) -> Path`** — saves the streamable `.tar.gz`.

### Writes (token required)

- **`publish(namespace, name, version, spec_dir, changelog="") -> ModuleManifest`** — uploads the
  spec directory (`gather_spec_files` collects yaml/csv/md/logo/logs, skipping parquets +
  `manifest.json`) and returns the compiled manifest.
- **`import_module(namespace, name, version, archive_path, *, changelog="", display=None) -> ModuleManifest`**
  — uploads a zip/tar.gz. `display` (`title/description/report_title/icon/color`) is used only for
  legacy parquet-only archives.

### Helper

- **`gather_spec_files(spec_dir) -> list[tuple[str, bytes]]`** — the uploadable (relative-name,
  bytes) pairs for a spec dir; excludes compiled `*.parquet` and `manifest.json`.

### Example

```python
from just_dna_marketplace import MarketplaceClient, MarketplaceError

with MarketplaceClient(url, token) as mkt:
    m = mkt.import_module("just-dna-seq", "coronary", "1.0.0", "coronary_v1.zip")
    print(m.artifact.digest)

    if mkt.lookup_by_digest(m.artifact.digest):
        print("already published")

    mkt.download("just-dna-seq", "coronary", "1.0.0", "./coronary")  # verifies or raises

    try:
        mkt.publish("just-dna-seq", "coronary", "1.0.0", "./spec")   # same version again
    except MarketplaceError as e:
        assert e.status_code == 409  # version_exists
```

---

## CLI: `marketplace-client`

All commands accept `--url` (or `$MARKETPLACE_URL`); write commands accept `--token`
(or `$MARKETPLACE_TOKEN`).

### `list`
```bash
marketplace-client list [--q TEXT] [--gene GENE] [--category CAT] [--sort name|downloads|recent]
```
Prints one line per module (`ns/name@latest [N variants, M genes] ↓downloads — title`).

### `download`
```bash
marketplace-client download NS NAME VERSION DEST          # extract + integrity-verify into DEST/
marketplace-client download NS NAME VERSION FILE.tar.gz --tarball   # save a single tar.gz
```

### `register`
```bash
marketplace-client register ACCOUNT [--install-id jdi1_…] [--difficulty 20]
```
Grinds an install-id (unless `--install-id` given), self-registers, and prints the account,
install-id, and API key. Save both; put the key in `MARKETPLACE_TOKEN`.

### `namespace-available` / `claim-namespace`
```bash
marketplace-client namespace-available alice-mods
marketplace-client claim-namespace alice-mods        # (token)
```

### `find-by-hash`
```bash
marketplace-client find-by-hash sha256:…                  # by digest
marketplace-client find-by-hash --manifest ./mod/manifest.json    # read digest from a local manifest
```
Exit code `1` (and "not published") if there are no matches.

### `publish`  *(token)*
```bash
marketplace-client publish NS NAME VERSION SPEC_DIR [--changelog "…"]
```
Uploads a spec directory (must contain `module_spec.yaml` + `variants.csv` + `studies.csv`). On
success it **stamps** the returned manifest into `SPEC_DIR/manifest.json`, so the local module is
afterwards discernible as published-by-you (identity + `published_at`).

### `import-module`  *(token)*
```bash
marketplace-client import-module NS NAME VERSION ARCHIVE.zip \
    [--changelog "…"] [--title …] [--description …] [--report-title …] [--icon …] [--color …]
```
Publishes from a zip/tar.gz. Display flags apply only to legacy parquet-only archives.

### `update-module-version`  *(token)*
```bash
marketplace-client update-module-version NS NAME VERSION SPEC_DIR [--changelog "…"]
```
Fetches the module's current latest, checks `VERSION` supersedes it (SemVer), and publishes. Errors
if the module doesn't exist yet (use `publish`) or `VERSION` isn't greater than latest.

---

## Server admin CLI (`marketplace`, needs `[server]`)

Not part of the client surface, but for completeness — run **on the server**, against its DB/storage:

```bash
marketplace serve --host 0.0.0.0 --port 8000
marketplace init-db
marketplace issue-key <account> -n <namespace>          # mint an API key
marketplace remove-module <ns> <name> [--yes]           # ops-only hard delete (not yank)
marketplace remove-namespace <ns> [--yes]               # purge + free the namespace
```
