# Changelog

All notable changes to **just-dna-marketplace**. Format follows
[Keep a Changelog](https://keepachangelog.com/); versions are [SemVer](https://semver.org/).

Full API: [API-REFERENCE.md](API-REFERENCE.md) · client: [CLIENT.md](CLIENT.md) · plan:
[ROADMAP.md](ROADMAP.md).

## [0.8.0] — 2026-07-09

Listing groups + reviews/audits + account profiles — additive, marketplace-layer catalog features.
No contract change (pins stay `>=0.3.0`); `just-dna-format` is untouched. New tables/columns are
created idempotently by `init_db`, so a live catalog upgrades in place.

### Added — listing groups
Server-owned namespace grouping behind the webui's tabs. Membership is defined server-side (not in a
consumer) so the webui, the CLI, and any client agree on what each tab contains.
- **`?group=` on `GET /modules`** — `all | featured | curated | popular | new | test`, each a preset
  over the existing primitives: `featured`→`featured=true`, `curated`→has an owner-highlighted review,
  `popular`→`sort=popular`, `new`→`sort=recent`, `all`→everything. A group wins over the equivalent
  raw `sort`/`featured` params.
- **Test/sandbox isolation.** Namespaces matching `MARKETPLACE_TEST_NAMESPACE_PATTERN` (default
  `^(sandbox|test)([-_]|$)`) are classified `test`: surfaced only under `?group=test` and **hidden
  from every other tab** (including the default listing). A test space stays reachable by exact
  `?namespace=`. The regex is server config, never a client-supplied param (consistency + no ReDoS
  surface).
- **`GET /api/v1/modules/groups`** — discovery endpoint returning `[{key, label, description}]` so a
  UI renders tabs from server truth instead of hardcoding.
- Client CLI: `marketplace-client list --group <tab>`.

### Added — reviews & audits
A marketplace-layer social record about a published version. **Not a module feature: the manifest is
untouched** (reviews are mutable social data; the manifest is the immutable, content-addressed
artifact).
- **Open, version-scoped reviews.** `PUT/DELETE /api/v1/modules/{ns}/{name}/versions/{v}/reviews`
  (bearer) — anyone authenticated posts one review per version: a `rating` (1-5) plus an optional
  audit `verdict` (`verified|concerns|rejected`) and `notes`. Re-posting replaces (one per account
  per version). `GET .../reviews` (and `GET /modules/{ns}/{name}/reviews` across versions) list them,
  highlighted first. Anonymous reads.
- **Owner highlight (SO accepted-answer style).** `PUT/DELETE
  .../versions/{v}/reviews/{reviewer}/highlight` — the **namespace owner** highlights the good
  reviews; any number may be highlighted ("the more the merrier"). A highlighted review is the trust
  signal that `?group=curated` and the card `curated` flag key on (and, once a reputation system
  lands, will accrue to the reviewer as demonstrated expertise).
- **Card fields** `review_count`, `avg_rating` (mean 1-5, null when unreviewed), and `curated` (has a
  highlighted review).

### Added — account profiles
The `accounts` row is the single user primitive (auth stays token-based; no separate `users` table).
- **`email`** (private — returned only from `whoami`, unique when set) and **`display_name`** (human
  name, distinct from the `name` handle) columns, plus a GitHub-style **`type`** discriminator
  (`user` | `org`) so one identity primitive can be a person or an organization.
- **`PATCH /api/v1/auth/whoami`** — the account edits its own `email`/`display_name` (omitted fields
  unchanged; `""` clears; duplicate email → `409 email_taken`). `whoami` now returns `type`,
  `display_name`, `email`. `type` is set at creation by the admin CLI, not self-editable.
- `marketplace issue-key` gains `--email`, `--display-name`, `--type user|org`.

### Note
- Grouping operates over the **module listing** (which modules show per tab). A namespace-browse view
  (list spaces with aggregate stats) was considered and deferred — not needed for the tabbed listing.
- Reviews are **version-scoped**: an audit vouches for specific bytes; a new version starts
  un-highlighted. Editing a review leaves the owner's highlight untouched.

## [0.7.1] — 2026-07-08

Adopts **just-dna-format / just-dna-compiler 0.3.0** (pins bumped to `>=0.3.0`) and adds the
automation and the client/server guard that a contract bump needs. The 0.3 columns are additive and
the server recompiles every spec, so published modules gain them on their next publish with no
migration.

### Added
- **`marketplace upgrade`** (+ `services/upgrade.py`) — back-populates the additive 0.3 axes
  (`direction`, `stat_significance`, `clin_sig`, and a trimmed `state`) from the legacy
  `state`/ClinVar booleans by applying the format's own `VariantRow.upgraded()` derivation, then
  re-publishes as the next PATCH through the normal server-side compile path. Dry-run by default;
  `--apply` publishes; `-n`/`-m` scope it. The predecessor is never mutated, the transform is
  idempotent, and the logo carries forward (logs/provenance do not — they describe the predecessor).
- **Server/client version-mismatch guard.** The server advertises its versions — `GET
  /api/v1/version` (`{api, marketplace, format, compiler}`) plus `X-Marketplace-Version` /
  `X-Format-Version` / `X-API-Version` on **every** response — and the client sends its own as
  request headers. Before publish/import/download the client calls `assert_compatible()` and raises
  `VersionMismatchError` (409) with an actionable message when the API version or the
  `just-dna-format` contract can't interoperate (same MAJOR; and same MINOR while `0.x`, since a 0.x
  minor moves the parquet schema / `artifact.digest` — the 0.2→0.3 case). A differing marketplace
  *app* version is **not** fatal (the API is path-versioned). Escape hatch
  `MARKETPLACE_SKIP_VERSION_CHECK=1` (or `MarketplaceClient(check_version=False)`);
  `marketplace-client version` prints both sides and the verdict. Logic in `version.py`.

### Changed
- **`revalidate` now reports `ok` / `upgradable` / `needs_upgrade` / `skipped`.** Because the 0.3
  columns are additive, a legacy module still *validates* — the new `upgradable` status flags a
  version whose 0.3 axes can be losslessly back-populated (re-publish with `marketplace upgrade`),
  distinct from `needs_upgrade` (fails the current validator). `--set-flag` marks both.
- Contract pins `just-dna-format` / `just-dna-compiler` → `>=0.3.0`.
- Coding-standards doc (`CLAUDE.md`): logging policy switched to stdlib `logging` (Eliot is being
  retired); the new `version.py` / `client.py` follow it.

## [0.6.0] — 2026-07-08

Community & discovery features. No `just-dna-format`/`just-dna-compiler` change (pins stay `>=0.2.0`).
All schema changes are additive `ALTER`s / new tables applied idempotently by `init_db`, so an
existing live catalog upgrades in place — a pre-0.6 single-owner namespace is backfilled as an
`owner` membership automatically.

### Added
- **GitHub-style stars.** `PUT`/`DELETE /api/v1/modules/{ns}/{name}/star` (auth) toggle a favourite;
  the stargazer count and the caller's `starred_by_me` appear on the card, and `?sort=stars` ranks
  by count. Idempotent (starring twice keeps one star). A `module_stars` table is the source of
  truth; `modules.stars` is its maintained cache.
- **Namespace membership (owner / contributor).** Namespaces are no longer single-owner. A
  `namespace_members` join table grants access: both roles publish/amend/yank, but only an **owner**
  can add/remove members, promote to owner, or revoke access. `GET/POST/DELETE
  /api/v1/namespaces/{ns}/members` (owner-gated mutations; last owner cannot be removed) and ops
  commands `marketplace add-member|remove-member|list-members`. Revocation is **namespace-scoped**
  (removes the membership), not a global API-key kill.
- **Popularity.** `modules.views` (bumped on a module-detail view) and `modules.search_hits` (bumped
  for every module surfaced in a search page) blend into `?sort=popular`.
- **Download & last-updated refinements.** Per-version download counts (`VersionSummary.downloads`);
  artifact-file fetches via `.../files/<parquet>` now count as downloads (so presigned/CDN redirects
  are counted while log/provenance/logo fetches are not); a distinct module-level `created_at`
  (first publish) surfaced on the card alongside `updated_at`. (Download counts and `updated_at`
  themselves already existed since 0.x — this release refines them.)

### Note
- New sort keys: `?sort=stars|popular` (in addition to `downloads|recent|name`).
- New rate-limit category `social` (star toggles), configurable via `MARKETPLACE_RATE_SOCIAL_PER_MIN`
  (default 30/min).

## [0.5.0] — 2026-07-07

Accommodates **just-dna-format / just-dna-compiler 0.2.0** (pins bumped to `>=0.2.0`). The DB stores
each version's whole `manifest.json`, so the new manifest fields round-trip with **no schema
migration**; this release *surfaces* and *serves* them.

### Added
- **Structured provenance + gene-panel surfacing.** A published spec's `provenance.json` (per-variant
  rationale) is compiled, hashed, and served at `.../files/provenance.json`; the manifest carries the
  lean `provenance` summary. A `panel` (gene-panel) declaration and `display.icon_set` round-trip and
  appear on the module card.
- **ClinVar stat surfacing.** `CardStats` gains `clinvar_count` / `pathogenic_count` / `benign_count`.
- **Module logo.** A published `logo.{png,jpg,jpeg}` is compiled out of `artifact.digest`, served at
  `.../files/<logo>`, included in the download tarball, and exposed as `logo_url` on the card
  (consumers fall back to `icon`/`icon_set` when absent).
- **`POST .../versions/{version}/logo`** — owner-scoped logo replacement, mirroring `amend-changelog`.
  Metadata-only: the artifact/digest — and any signature over it — stay immutable, so **no version
  bump**. Client `amend_logo(...)` + `marketplace-client amend-logo`.
- **Optional Ed25519 signing (SPEC §5).** Set `MARKETPLACE_SIGNING_KEY` to an Ed25519 private-key PEM
  and the server signs each version's `artifact.digest`; `GET /api/v1/pubkey` serves the public key
  for clients to pin. `VersionSummary.signed` flags signed versions; `client.download(...,
  public_key=...)` enforces a pinned key. Unset (default) → unsigned, 0.4 behaviour unchanged.

## [0.4.5] — 2026-07-07

### Added
- **`GET /health` now reports `version` and `storage`** — so you can confirm which build is live
  without shell access to the box (`{"status":"ok","version":"0.4.5","storage":"hf"}`). The version
  is read from installed package metadata (`importlib.metadata`), not hardcoded — the FastAPI
  `app.version` (and `/openapi.json`) track it automatically on every bump.

### Note
- This does **not** change the large-publish path. A publish still couples one HTTP connection to
  the full server-side compile (~90 s for genome-wide modules); if that connection is severed
  (proxy header-timeout, or the worker dying — e.g. OOM on a 674k-variant compile) the client sees
  `RemoteProtocolError: Server disconnected`. Decoupling publish (`202` + background compile + poll)
  is tracked in ROADMAP 0.5.

## [0.4.4] — 2026-07-07

### Added
- **`marketplace remove-version <ns> <name> <v>`** — ops-only hard delete of a *single* version
  (row + facet rows + artifacts), recomputing the module's latest. Complements the whole-module
  `remove-module` and per-version `yank` — for surgically dropping one bad/partial version so it can
  be re-uploaded. `repo.delete_version(...)`.

## [0.4.3] — 2026-07-07

### Fixed
- **Publish no longer blocks the event loop.** `compile_module` (CPU-heavy — up to minutes for
  large modules) now runs in a worker thread (`run_in_threadpool`) instead of synchronously in the
  async handler. Previously a big publish froze the whole server for the duration and the
  connection was dropped mid-request (`RemoteProtocolError: Server disconnected`), even though the
  compile eventually finished with 201. Fixes publishing large modules (e.g. `pathogenic`, ~89 s).
- SQLite `busy_timeout=5000` to absorb brief write contention now that publishes run concurrently.

### Changed
- Client HTTP timeout default 120 s → **600 s**, and env-configurable via `MARKETPLACE_TIMEOUT`.

> Deployment note: a reverse proxy in front of the server (Caddy) must also allow long upstream
> responses for large publishes; otherwise it will cut the connection before the compile finishes.

## [0.4.2] — 2026-07-07

### Added
- **Amend changelog** — `PATCH /modules/{ns}/{name}/versions/{v}` updates a published version's
  changelog (metadata only; the artifact/digest stay immutable — not a re-publish). Owner-only,
  `append` option. Client `amend_changelog(...)` + CLI `amend-changelog`.

## [0.4.1] — 2026-07-07

### Added
- **Optional JWT sessions** — `POST /auth/tokens` exchanges a static API key for a short-lived JWT,
  also accepted as a bearer. Backwards-compatible: static keys always work; JWT is off unless
  `jwt_secret` (≥32 bytes) is set (`501 jwt_disabled` otherwise). Config: `jwt_secret`,
  `jwt_ttl_seconds`.

### Removed
- **Prebuilt-parquet upload / "trust-but-verify" mode** — dropped as planning legacy. It existed to
  avoid bundling the compiler, but the server recompiles from spec, so there's no prebuilt artifact
  to ingest. (Reproducibility, if ever needed, is better checked via parquet frame-shape +
  canonically-sorted content than byte digests.)

## [0.4.0] — 2026-07-07

Moderation, ops hardening, HuggingFace storage, and the webui page deliverable.

### Added
- **Featured namespaces** — `featured` flag; featured modules float to the top of every listing,
  `?featured=true` restricts, cards carry `featured`. Admin CLI `feature`/`unfeature`.
- **Blacklisted namespaces** — hidden from default `GET /modules`/search; reachable via
  `?namespace=`, `?include_blacklisted=true`, or direct detail. Admin CLI `blacklist`/`unblacklist`.
  New list filters: `namespace`, `featured`, `include_blacklisted`.
- **Key revocation** — `marketplace revoke-key` / `revoke-account`.
- **Rate limiting** (SPEC §7) — in-memory token buckets per caller × category on
  search/download/publish; `429 rate_limited` + `Retry-After`. Config: `rate_limit_enabled`,
  `rate_publish_per_hour`, `rate_download_per_hour`, `rate_search_per_min`.
- **`HfStorage` backend** — HF dataset repo (`data/{ns}/{name}/{version}/…`); commit writes,
  `HfFileSystem` reads, `302` to HF `resolve` URLs. Select with `storage_backend=hf`.
- **Docs** — `WEBUI-MARKETPLACE.md` (marketplace-page deliverable for the webui).

### Migrations
- `namespaces.featured` / `namespaces.blacklisted` columns (idempotent, in-place).

### Deferred (see ROADMAP 0.4)
- Ed25519 signing, presigned PUT, prebuilt "trust-but-verify" mode, JWT/OAuth + orgs, download
  analytics. → 0.5: Postgres, FTS5/search. Excluded: S3/MinIO.

## [0.3.0] — 2026-07-07

Community-first, self-service onboarding — publish from the just-dna-lite UI without leaving the app.

### Added
- **Install-id proof-of-work** (`installid.generate_install_id` / `validate_install_id`, exported
  at top level) — the lite app mints one at first run; SHA-256 with ≥ `install_id_difficulty`
  (default 20) leading zero bits. Deters random/bulk spambot ids; O(1) to verify.
- **Self-registration** — `POST /api/v1/auth/register {install_id, account}` mints an account +
  API key (one per install-id). Gated by `allow_self_register` (default on).
- **Namespace claim** — `GET /api/v1/namespaces/{ns}` (availability) + `POST /api/v1/namespaces`
  (claim), up to `namespaces_per_account` (default 5) per account; `409 namespace_taken` /
  `403 namespace_limit_reached`.
- **Batch digest lookup** — `POST /api/v1/modules/lookup {digests:[…]}` (cap `lookup_batch_max`) to
  classify many local modules at once.
- **Client + CLI** — `MarketplaceClient.register / namespace_available / claim_namespace /
  lookup_by_digests`; `marketplace-client register | namespace-available | claim-namespace`.
- Provenance: `marketplace-client publish` now **stamps** the returned manifest into the local spec
  dir so a module is discernible as published-by-you (no `module_spec.yaml` change).
- Config: `allow_self_register`, `install_id_difficulty`, `namespaces_per_account`,
  `lookup_batch_max`.

### Changed
- DB: `accounts.install_id` (unique, nullable) added via an idempotent in-place migration.

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

[0.8.0]: #080--2026-07-09
[0.7.1]: #071--2026-07-08
[0.6.0]: #060--2026-07-08
[0.5.0]: #050--2026-07-07
[0.4.5]: #045--2026-07-07
[0.4.4]: #044--2026-07-07
[0.4.3]: #043--2026-07-07
[0.4.2]: #042--2026-07-07
[0.4.1]: #041--2026-07-07
[0.4.0]: #040--2026-07-07
[0.3.0]: #030--2026-07-07
[0.2.1]: #021--2026-07-07
[0.2.0]: #020--2026-07-07
[0.1.0]: #010--2026-07-06
