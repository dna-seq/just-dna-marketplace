# just-dna-registry — Client Reference

The reference client is the **default install** of `just-dna-registry` — import it instead of
re-implementing REST calls + integrity verification. It ships as a Python library
(`RegistryClient`) and an equivalent CLI (`registry-client`). Wire protocol:
[API-REFERENCE.md](API-REFERENCE.md).

## Install

```bash
pip install just-dna-registry            # client only (httpx + just-dna-format)
pip install just-dna-registry[server]    # + the server (FastAPI app, compiler, admin CLI)
```

## Configuration

Both the Python client and the CLI take the base URL and (for writes) an API key. The CLI reads
them from flags, then environment, then a local `.env`:

| Setting | Env var | CLI flag | Default |
|---|---|---|---|
| Base URL | `REGISTRY_URL` | `--url` | `http://127.0.0.1:8000` |
| API key | `REGISTRY_TOKEN` | `--token` | — (required for publish/import/update) |

```bash
export REGISTRY_URL=https://module-registry.just-dna.life
export REGISTRY_TOKEN=mk_live_…
```

## Python ↔ CLI at a glance

| Capability | `RegistryClient` method | `registry-client` command | Auth |
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
| Amend changelog | `amend_changelog(ns, name, v, text, append=)` | `amend-changelog` | bearer |

---

## Python: `RegistryClient`

```python
from just_dna_registry import RegistryClient, RegistryError

with RegistryClient("https://module-registry.just-dna.life", token="mk_live_…") as mkt:
    ...
```

**`RegistryClient(base_url, token=None, timeout=120.0, transport=None)`** — a context manager
(closes the underlying `httpx.Client`). `transport` is for tests (e.g. an ASGI transport).
Non-2xx responses raise **`RegistryError(status_code, detail)`**.

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
  top level: `from just_dna_registry import generate_install_id`).
- **`namespace_available(namespace) -> dict`** — `{namespace, valid, available}`.
- **`claim_namespace(namespace) -> dict`** *(token)* — claims it for your account
  (`{namespace, owner, already_owned}`); raises `RegistryError` `409`/`403` if taken/over-limit.
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

### Identity & profile (token)

- **`whoami() -> dict`** — `{account, namespaces, type, display_name, avatar_url, email}` (`email`
  only ever returned to the account itself).
- **`update_profile(*, email=None, display_name=None, avatar_url=None) -> dict`** — edit your own
  profile; only the fields passed are sent, `""` clears one. `type` is not self-editable.

### Social & moderation (token)

- **`star(ns, name)` / `unstar(ns, name) -> dict`** — toggle a favourite (idempotent).
- **`reviews(ns, name, version=None) -> list[dict]`** — a module's (or one version's) reviews,
  highlighted first (anonymous).
- **`review(ns, name, version, *, rating, verdict=None, notes=None) -> list[dict]`** — post/update
  your review of a version (one per account per version); returns the version's review list.
- **`delete_review(ns, name, version) -> list[dict]`** — remove your own review.
- **`highlight_review(ns, name, version, reviewer, *, highlighted=True) -> list[dict]`** — owner
  highlights (or un-highlights) a review — the `curated` signal.
- **`yank(ns, name, version)` / `unyank(...) -> dict`** — owner: drop from listings/`latest` (kept
  fetchable) or reverse it.
- **`members(ns) -> list[dict]`**, **`add_member(ns, account, role="contributor")`**,
  **`remove_member(ns, account) -> dict`** — namespace membership (mutations are owner-only).

### Discovery & stats

- **`groups() -> list[dict]`** — the listing tabs `[{key, label, description}]`.
- **`catalog_stats(namespace=None, *, group=None) -> dict`** — aggregate totals (modules,
  namespaces, downloads, stars, views, reviews, curated, variants, studies, genes) by paging the
  listing; there is no dedicated stats endpoint, so this rolls up the card fields.

### Helper

- **`gather_spec_files(spec_dir) -> list[tuple[str, bytes]]`** — the uploadable (relative-name,
  bytes) pairs for a spec dir; excludes compiled `*.parquet` and `manifest.json`.

### Example

```python
from just_dna_registry import RegistryClient, RegistryError

with RegistryClient(url, token) as mkt:
    m = mkt.import_module("just-dna-seq", "coronary", "1.0.0", "coronary_v1.zip")
    print(m.artifact.digest)

    if mkt.lookup_by_digest(m.artifact.digest):
        print("already published")

    mkt.download("just-dna-seq", "coronary", "1.0.0", "./coronary")  # verifies or raises

    try:
        mkt.publish("just-dna-seq", "coronary", "1.0.0", "./spec")   # same version again
    except RegistryError as e:
        assert e.status_code == 409  # version_exists
```

---

## CLI: `registry-client`

All commands accept `--url` (or `$REGISTRY_URL`); write commands accept `--token`
(or `$REGISTRY_TOKEN`).

### `list`
```bash
registry-client list [--q TEXT] [--gene GENE] [--category CAT] [--sort name|downloads|recent]
```
Prints one line per module (`ns/name@latest [N variants, M genes] ↓downloads — title`).

### `download`
```bash
registry-client download NS NAME VERSION DEST          # extract + integrity-verify into DEST/
registry-client download NS NAME VERSION FILE.tar.gz --tarball   # save a single tar.gz
```

### `register`
```bash
registry-client register ACCOUNT [--install-id jdi1_…] [--difficulty 20]
```
Grinds an install-id (unless `--install-id` given), self-registers, and prints the account,
install-id, and API key. Save both; put the key in `REGISTRY_TOKEN`.

### `namespace-available` / `claim-namespace`
```bash
registry-client namespace-available alice-mods
registry-client claim-namespace alice-mods        # (token)
```

### `find-by-hash`
```bash
registry-client find-by-hash sha256:…                  # by digest
registry-client find-by-hash --manifest ./mod/manifest.json    # read digest from a local manifest
```
Exit code `1` (and "not published") if there are no matches.

### `publish`  *(token)*
```bash
registry-client publish NS NAME VERSION SPEC_DIR [--changelog "…"]
```
Uploads a spec directory (must contain `module_spec.yaml` + `variants.csv` + `studies.csv`). On
success it **stamps** the returned manifest into `SPEC_DIR/manifest.json`, so the local module is
afterwards discernible as published-by-you (identity + `published_at`).

### `import-module`  *(token)*
```bash
registry-client import-module NS NAME VERSION ARCHIVE.zip \
    [--changelog "…"] [--title …] [--description …] [--report-title …] [--icon …] [--color …]
```
Publishes from a zip/tar.gz. Display flags apply only to legacy parquet-only archives.

### `update-module-version`  *(token)*
```bash
registry-client update-module-version NS NAME VERSION SPEC_DIR [--changelog "…"]
```
Fetches the module's current latest, checks `VERSION` supersedes it (SemVer), and publishes. Errors
if the module doesn't exist yet (use `publish`) or `VERSION` isn't greater than latest.

---

## Server admin CLI (`registry`, needs `[server]`)

Not part of the client surface, but for completeness — run **on the server**, against its DB/storage:

```bash
registry serve --host 0.0.0.0 --port 8000
registry init-db
registry issue-key <account> -n <namespace>          # mint an API key
registry revoke-key <key>                            # invalidate a leaked key
registry revoke-account <account> [--yes]            # invalidate all of an account's keys
registry feature <ns> / unfeature <ns>               # curate: float a namespace to the top
registry blacklist <ns> / unblacklist <ns>           # moderate: hide from default listings
registry remove-version <ns> <name> <v> [--yes]      # hard-delete ONE version (not yank)
registry remove-module <ns> <name> [--yes]           # hard-delete a whole module (all versions)
registry remove-namespace <ns> [--yes]               # purge + free the namespace
registry issue-key <acct> --email … --display-name … --avatar-url … --type user|org
registry export-keys [-o auth.json]                  # dump accounts + API keys + namespaces (SECRET)
registry import-keys auth.json                       # restore the auth graph (idempotent)
registry reset-db [--keep-keys|--wipe-keys]          # wipe catalog; keeps keys by default; types RESET
```

`export-keys`/`import-keys` move the **auth graph** (accounts, API keys, namespaces, memberships)
between DBs/environments; the export contains live tokens, so protect it. `reset-db` clears the
catalog projection for a fresh start while keeping your keys (so you don't lock yourself out); it
requires typing `RESET` and does not touch artifact storage. The **signing key** is a PEM file
(`REGISTRY_SIGNING_KEY`), never in the DB — copy it directly to reuse across environments.
