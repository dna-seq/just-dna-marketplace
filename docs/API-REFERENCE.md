# just-dna-marketplace — REST API Reference

Exhaustive reference for the marketplace HTTP API (v1). For the design rationale see
[SPEC.md](SPEC.md); for the reference client see [CLIENT.md](CLIENT.md).

- **Base URL:** `https://module-marketplace.just-dna.life`
- **API prefix:** `/api/v1` (health lives at the root, `/health`)
- **Interactive docs:** `/docs` (Swagger UI), `/openapi.json`
- **Content types:** responses are JSON unless noted; publish/import use `multipart/form-data`;
  file downloads are `application/octet-stream` (or `302` redirect) and tarballs `application/gzip`.

## Authentication

Static API keys via a bearer header:

```
Authorization: Bearer mk_live_…
```

Keys are minted server-side with `marketplace issue-key <account> -n <namespace>` (or self-service
via `POST /auth/register`). A key's account owns one or more **namespaces**; publishing/yanking
under a namespace requires ownership. **Reads are anonymous;** only publish, import, yank, and
`whoami` require a token.

**Optional JWT sessions.** When the server sets `jwt_secret`, `POST /auth/tokens` exchanges an API
key for a short-lived JWT that is also accepted as a bearer. Static API keys always work — JWT is
purely additive; if `jwt_secret` is unset, `POST /auth/tokens` returns `501 jwt_disabled`.

## Pagination

List endpoints accept `?page` (≥1, default 1) and `?per_page` (≥1, default 20, clamped to **100**)
and return an envelope:

```json
{ "items": [ … ], "total": 47, "page": 1, "per_page": 20 }
```

## Errors

FastAPI shape: `{"detail": …}`. Simple guards use a string code; publish/import validation failures
use an object.

| Status | `detail` | When |
|---|---|---|
| `401` | `missing_bearer_token` / `invalid_token` | no/invalid `Authorization` on an authed route |
| `403` | `not_namespace_member` | token isn't a member of the path namespace |
| `403` | `not_namespace_owner` | member action requires the `owner` role (member management) |
| `404` | `module_not_found` / `version_not_found` / `file_not_found` / `account_not_found` / `not_a_member` | unknown module/version/file/account/member |
| `409` | `version_exists` | re-publishing an existing `(ns, name, version)` (immutable) |
| `409` | `last_owner` | removing a namespace's only owner |
| `422` | `invalid_version` | version isn't SemVer `MAJOR.MINOR.PATCH` |
| `422` | `{ "error": "<code>", "errors": [...], "warnings": [...] }` | spec/import failure (see below) |
| `429` | `rate_limited` | token bucket exhausted (search/download/publish); `Retry-After` header |

Publish/import `422.error` codes: `missing_spec_files`, `invalid_spec` (carries
`ValidationResult.errors`/`warnings`), `compile_failed`, `name_mismatch`, and for import
`unsafe_archive`, `bad_archive`, `no_module_content`.

---

## Endpoints

| # | Method | Path | Auth | Purpose |
|---|---|---|---|---|
| 1 | GET | `/health` | — | Liveness |
| 2 | GET | `/api/v1/modules` | — | List / search (card grid) |
| 3 | GET | `/api/v1/modules/lookup?digest=` | — | Find versions by artifact digest |
| 4 | GET | `/api/v1/modules/{ns}/{name}` | — | Module detail |
| 5 | GET | `/api/v1/modules/{ns}/{name}/versions` | — | Version list |
| 6 | GET | `/api/v1/modules/{ns}/{name}/versions/{v}/manifest` | — | Full manifest |
| 7 | GET | `/api/v1/modules/{ns}/{name}/versions/{v}/logs` | — | Provenance/run logs listing |
| 8 | GET | `/api/v1/modules/{ns}/{name}/versions/{v}/files/{path}` | — | Fetch one file (parquet/log/input) |
| 9 | GET | `/api/v1/modules/{ns}/{name}/versions/{v}/download` | — | Per-file descriptors or tar.gz |
| 10 | POST | `/api/v1/modules/{ns}/{name}/versions` | bearer | Publish (multipart spec) |
| 11 | POST | `/api/v1/modules/{ns}/{name}/versions/import` | bearer | Publish from zip/tar.gz archive |
| 12 | POST | `/api/v1/modules/{ns}/{name}/versions/{v}/yank` | bearer | Yank / un-yank a version |
| 13 | GET | `/api/v1/auth/whoami` | bearer | Identity + owned namespaces |
| 14 | POST | `/api/v1/auth/register` | install-id | Self-register → account + API key |
| 15 | GET | `/api/v1/namespaces/{ns}` | — | Namespace availability |
| 16 | POST | `/api/v1/namespaces` | bearer | Claim an available namespace |
| 17 | POST | `/api/v1/modules/lookup` | — | Batch digest lookup |
| 18 | POST | `/api/v1/auth/tokens` | api key | Exchange an API key for a JWT (optional) |
| 19 | PATCH | `/api/v1/modules/{ns}/{name}/versions/{v}` | bearer | Amend the version changelog (metadata) |
| 20 | POST | `/api/v1/modules/{ns}/{name}/versions/{v}/logo` | bearer | Replace the version logo (metadata, out of digest) |
| 21 | GET | `/api/v1/pubkey` | — | Ed25519 public key for verifying signed manifests |
| 22 | PUT | `/api/v1/modules/{ns}/{name}/star` | bearer | Star a module (favourite) |
| 23 | DELETE | `/api/v1/modules/{ns}/{name}/star` | bearer | Remove the caller's star |
| 24 | GET | `/api/v1/namespaces/{ns}/members` | bearer | List namespace members + roles |
| 25 | POST | `/api/v1/namespaces/{ns}/members` | bearer (owner) | Add / promote a member |
| 26 | DELETE | `/api/v1/namespaces/{ns}/members/{account}` | bearer (owner) | Revoke a member's access |

---

### 1. `GET /health`
`200 → {"status": "ok"}`. No prefix, no auth.

### 2. `GET /api/v1/modules`
List/search the catalog (one **card** per module, its latest non-yanked version).

Query params: `q` (title/description substring), `category`, `gene`, `genome_build`, `owner`,
`license` (exact facet matches), `namespace` (restrict to one namespace), `featured` (`true` →
only featured), `include_blacklisted` (`true` → include hidden namespaces), `sort` = `name`
(default) | `downloads` | `recent` | `stars` | `popular`, plus `page`, `per_page`. Facet filters
match modules with a non-yanked version carrying that gene/category.

`200 → Page<ModuleCard>`. **Featured** modules float to the top of every sort (card has
`featured: bool`). **Blacklisted** namespaces are omitted by default — returned only with
`include_blacklisted=true` or an explicit `namespace=` (moderation, not deletion). Card
`stats.genes` is **truncated** (top 3); the full list is in the detail and manifest. Rate-limited
(`search` bucket). Each listed module also takes one `search_hit` (feeds `sort=popular`). When
called **with** a bearer token, `starred_by_me` reflects the caller; anonymous reads leave it
`false`.

```json
{
  "items": [{
    "namespace": "just-dna-seq", "name": "coronary", "title": "Coronary",
    "description": "…", "icon": "heart", "color": "#db2828",
    "latest_version": "2.0.0", "genome_build": "GRCh38", "license": null, "owner": "just-dna-seq",
    "stats": {"variant_count": 16, "study_count": 5, "gene_count": 8,
              "genes": ["APOE","LPA","PCSK9"], "categories": ["cardio"]},
    "downloads": 214, "stars": 12, "views": 340, "starred_by_me": false,
    "created_at": "2026-05-01T09:00:00Z", "updated_at": "2026-07-06T20:38:01Z"
  }],
  "total": 1, "page": 1, "per_page": 20
}
```

Sort keys: `downloads` (module download total), `recent` (`updated_at`), `stars` (stargazer count),
`popular` (blended `views + search_hits`).

### 3. `GET /api/v1/modules/lookup?digest=sha256:…`
Find published versions whose `artifact.digest` matches (content-identity / "already published?"
check). `digest` is required. `200 →`

```json
{ "digest": "sha256:…", "matches": [ {"namespace":"just-dna-seq","name":"coronary","version":"1.0.0","yanked":false} ] }
```

`matches` is `[]` if none (not a 404).

### 4. `GET /api/v1/modules/{ns}/{name}`
`200 → ModuleDetail` = the card **plus** `readme` (MODULE.md text), the **full** `stats.genes`, the
embedded `versions` array (`VersionSummary[]`, includes yanked), and `latest_manifest` (the full
`ModuleManifest` inline). `404 module_not_found`. Each successful detail view increments the
module's `views` counter (feeds `sort=popular`); with a bearer token the card's `starred_by_me`
reflects the caller.

### 5. `GET /api/v1/modules/{ns}/{name}/versions`
`200 → Page<VersionSummary>` (paginated). `404 module_not_found`.

```json
{"version":"2.0.0","artifact_digest":"sha256:…","compile_success":true,"yanked":false,
 "downloads":214,"created_at":"…","changelog":"…","manifest_url":"/api/v1/modules/…/versions/2.0.0/manifest"}
```

### 6. `GET /api/v1/modules/{ns}/{name}/versions/{v}/manifest`
`200 →` the full [`ModuleManifest`](#modulemanifest). `404 version_not_found`.

### 7. `GET /api/v1/modules/{ns}/{name}/versions/{v}/logs`
`200 → {"items": [{"name":"v2.log","sha256":"sha256:…","size":1059987,"url":"…/files/v2.log"}]}`.
Empty `items` if the version has no logs. `404 version_not_found`.

### 8. `GET /api/v1/modules/{ns}/{name}/versions/{v}/files/{path}`
Fetch a single file recorded in the manifest — an artifact parquet, a provenance log (nested paths
allowed, e.g. `logs/reviewer.log`), or a spec input (`variants.csv`). `{path}` is a catch-all.
- `200` `application/octet-stream` (local storage streams the bytes), **or** `302` redirect to a
  CDN/presigned URL (external storage backends).
- `404 version_not_found` / `404 file_not_found` (path not in the manifest listing).
- Fetching an **artifact file** (a `manifest.artifact.files` entry, e.g. `weights.parquet`)
  increments the module + version `downloads` counters — so presigned/CDN redirects of the real
  bytes are counted. Fetching a log/provenance/logo file does **not** count.

### 9. `GET /api/v1/modules/{ns}/{name}/versions/{v}/download`
Increments the module's `downloads` counter **and** the version's `downloads` counter. `?format=`:
- `files` (default) → `200 {"digest":"sha256:…","files":[{"name","url","sha256","size"}]}` — the
  artifact files for verify-then-install; `url` points at endpoint 8 (or an external URL).
- `tarball` → `200` `application/gzip` (`Content-Disposition: attachment; filename="{name}-{v}.tar.gz"`),
  a streamable tar.gz of the whole version (`manifest.json` + artifact + logs + inputs).

`404 version_not_found`.

### 10. `POST /api/v1/modules/{ns}/{name}/versions`  *(bearer)*
Publish a new version. `multipart/form-data`:
- `version` (form, required) — SemVer.
- `changelog` (form, optional).
- `files` (one or more file parts) — the **spec**: `module_spec.yaml` + `variants.csv` +
  `studies.csv` required; `MODULE.md`, `logo.*`, and logs (`*.log`, `logs/*.log`) optional. Nested
  names are honored (`logs/reviewer.log`).

Flow: ownership → version format → immutability → `validate_spec` → `compile_module`
(`compiled_by="marketplace-server"`) → fill marketplace fields → store (version-scoped) → index.
The spec's `module.name` must equal the path `{name}` (`422 name_mismatch`).

`201 →` the full `ModuleManifest`. Errors: `401`, `403 not_namespace_member`,
`422 invalid_version`, `409 version_exists`, `422 {error: missing_spec_files|invalid_spec|compile_failed|name_mismatch}`.

### 11. `POST /api/v1/modules/{ns}/{name}/versions/import`  *(bearer)*
Publish from a **zip or tar.gz** archive (in-house packaging / legacy import). `multipart/form-data`:
- `version` (form, required), `changelog` (form, optional).
- `archive` (file, required) — a `.zip` / `.tar.gz`.
- Display metadata (form, optional): `title`, `description`, `report_title`, `icon`, `color` —
  used only for **legacy parquet-only** archives (reverse-engineered before recompiling).

A spec archive (contains `module_spec.yaml`) is recompiled directly; a legacy archive (only
`weights.parquet`, no spec) is reverse-engineered via `reverse_module` then recompiled. Extraction
is path-traversal-safe. Same guards/response as endpoint 10, plus `422 {error: unsafe_archive|bad_archive|no_module_content}`.

### 12. `POST /api/v1/modules/{ns}/{name}/versions/{v}/yank`  *(bearer)*
Body (optional JSON): `{"yanked": true}` (default `true`; send `false` to un-yank). Owner-only.
Yank drops the version from default listings and `latest` but keeps its manifest/artifact
fetchable; `latest_version` recomputes over the remaining non-yanked versions.

`200 → {"namespace","name","version","yanked"}`. Errors: `401`, `403`, `404 version_not_found`.

### 19. `PATCH /api/v1/modules/{ns}/{name}/versions/{v}`  *(bearer)*
Amend a published version's **changelog** — descriptive metadata only; the artifact and its
`digest` are immutable and untouched (this is *not* a re-publish). Owner-only. Body
`{"changelog": "…", "append": false}` (`append=true` adds to the existing changelog).
`200 → {"namespace","name","version","changelog"}`. Errors: `401`, `403 not_namespace_member`,
`404 version_not_found`.

### 20. `POST /api/v1/modules/{ns}/{name}/versions/{v}/logo`  *(bearer)*
Replace a version's **logo** — multipart `logo` file (`png`/`jpg`/`jpeg`). Descriptive metadata only:
the logo is out of `artifact.digest`, so the content identity (and any signature over it) stays
immutable and there is **no version bump**. Owner-only. `200 → {"namespace","name","version","logo":
{"name","sha256","size"}}`. Errors: `401`, `403 not_namespace_member`, `404 version_not_found`,
`422 invalid_logo` (bad extension). Cards expose the served logo as `logo_url`; consumers fall back
to `icon`/`icon_set` when a module ships none.

### 21. `GET /api/v1/pubkey`
The server's Ed25519 **public key** for verifying signed manifests (SPEC §5). `200 → {"algorithm":
"ed25519", "public_key": "<base64>"}` when the server is configured to sign (`MARKETPLACE_SIGNING_KEY`
set); `404 signing_not_configured` otherwise. Pin this key and pass it to the client's verify step to
defend against a compromised storage backend. Signed versions are flagged `signed: true` in the
versions list; the `revalidate` audit flags contract-drifted versions `needs_upgrade: true`.

### 22–23. `PUT` / `DELETE /api/v1/modules/{ns}/{name}/star`  *(bearer)*
Star (favourite) a module GitHub-style, or remove the caller's star. Both are **idempotent** (a
double `PUT` keeps exactly one star; a `DELETE` on an unstarred module is a no-op). `200 →
{"namespace","name","stars","starred_by_me"}` where `stars` is the total stargazer count.
Errors: `401`, `404 module_not_found`. Rate-limited (`social` bucket). Sort the catalog by count
with `GET /api/v1/modules?sort=stars`.

### 24. `GET /api/v1/namespaces/{ns}/members`  *(bearer)*
List a namespace's members. Any **member** (owner or contributor) may read. `200 → {"namespace":
"…", "members": [{"account": "alice", "role": "owner"}, {"account": "bob", "role": "contributor"}]}`.
Errors: `401`, `403 not_namespace_member`.

### 25. `POST /api/v1/namespaces/{ns}/members`  *(bearer — owner)*
Add or promote an account in a namespace. **Owner-only.** Body `{"account": "bob", "role":
"contributor"}` (`role` = `owner` | `contributor`, default `contributor`; re-posting an existing
member updates the role). Both roles can publish/amend/yank; only owners manage membership.
`201 → {"namespace","members":[…]}` (the updated roster). Errors: `401`, `403 not_namespace_owner`,
`404 account_not_found`, `422 invalid_role`.

### 26. `DELETE /api/v1/namespaces/{ns}/members/{account}`  *(bearer — owner)*
Revoke an account's access to a namespace — removes the membership row. **Owner-only.** This is
**namespace-scoped**, not a global API-key revocation: the account keeps its key and any other
namespaces. `200 → {"namespace","members":[…]}`. Errors: `401`, `403 not_namespace_owner`,
`404 account_not_found` / `404 not_a_member`, `409 last_owner` (cannot remove a namespace's only
owner). Global key/account revocation stays an ops-CLI action (`marketplace revoke-key` /
`revoke-account`).

### 13. `GET /api/v1/auth/whoami`  *(bearer)*
`200 → {"account": "just-dna-seq", "namespaces": ["just-dna-seq"]}` — `namespaces` is every
namespace the caller is a member of (owner or contributor). `401` on missing/invalid token.

### 18. `POST /api/v1/auth/tokens`
Optional JWT session. Body `{"api_key": "mk_live_…"}`. `200 → {"token": "<jwt>", "token_type":
"Bearer", "expires_in": 86400}`. Errors: `501 jwt_disabled` (no `jwt_secret` configured),
`401 invalid_token` (unknown API key). The returned JWT is accepted anywhere a bearer is.

### 14. `POST /api/v1/auth/register`
Self-service onboarding (community-first). Body `{"install_id": "jdi1_…", "account": "alice"}`.
The `install_id` is a proof-of-work token minted by the just-dna-lite app at first run (SHA-256 has
≥ `install_id_difficulty` leading zero bits). One account per install-id — re-registering an
install-id just issues a fresh key for its existing account.

`201 → {"token": "mk_live_…", "account": "alice", "namespaces": []}`. Errors:
`403 self_register_disabled` (when `allow_self_register=false`), `422 invalid_install_id` (bad PoW),
`422 invalid_account` (handle isn't a valid slug), `409 account_taken`.

### 15. `GET /api/v1/namespaces/{ns}`
`200 → {"namespace": "alice-mods", "valid": true, "available": true}`. Public. `valid` reflects the
slug rule (`^[a-z0-9][a-z0-9-]*$`); `available` is false once claimed.

### 16. `POST /api/v1/namespaces`  *(bearer)*
Claim an available namespace for the caller's account. Body `{"namespace": "alice-mods"}`.
`201 → {"namespace": "alice-mods", "owner": "alice", "already_owned": false}` (idempotent if you
already own it → `already_owned: true`). Errors: `401`, `422 invalid_namespace`,
`409 namespace_taken` (owned by someone else), `403 namespace_limit_reached` (account at
`namespaces_per_account`, default 5).

### 17. `POST /api/v1/modules/lookup`
Batch of endpoint 3. Body `{"digests": ["sha256:…", …]}` (capped at `lookup_batch_max`, default
256). `200 → {"results": [{"digest": "sha256:…", "matches": [{namespace,name,version,yanked}]}]}`.
Lets a consumer classify many local modules (provenance / "already published?") in one request —
digests are already in each module's `manifest.json`, so no client-side hashing.

---

## Schemas

### ModuleCard
`namespace, name, title, description, icon, icon_set, color, logo_url, latest_version, genome_build,
license, owner, stats: CardStats, downloads, stars, views, created_at, updated_at, starred_by_me,
featured`. `stars`/`views` are counters; `starred_by_me` is true only when the request carried a
bearer for an account that starred the module; `created_at` is the first-publish time,
`updated_at` advances on every republish.

### CardStats
`variant_count, study_count, gene_count, genes: string[], categories: string[]`. In cards `genes`
is truncated to 3; in detail/manifest it's the full list.

### VersionSummary
`version, artifact_digest, compile_success, yanked, signed, needs_upgrade, downloads, created_at,
changelog, manifest_url`. `downloads` is the per-version download count.

### ModuleDetail
`ModuleCard` fields + `readme: string`, `versions: VersionSummary[]`, `latest_manifest: ModuleManifest`.

### WhoAmI
`account: string, namespaces: string[]` (every namespace the account is a member of).

### MemberList
`namespace: string, members: [{account: string, role: "owner"|"contributor"}]`.

### StarStatus
`namespace: string, name: string, stars: int, starred_by_me: bool`.

### ModuleManifest  {#modulemanifest}
The source-of-truth contract (from `just-dna-format`; the DB is a projection of it):

```json
{
  "manifest_version": "1.0", "schema_version": "1.0",
  "identity": {"namespace": "just-dna-seq", "name": "coronary", "version": "1.0.0",
               "canonical_id": "just-dna-seq/coronary@1.0.0"},
  "display": {"title": "Coronary", "description": "…", "report_title": "…",
              "icon": "heart", "color": "#db2828"},
  "genome_build": "GRCh38", "curator": "…", "method": "…", "license": null,
  "owner": "just-dna-seq", "authors": [], "created_at": "…", "published_at": "…",
  "stats": {"variant_count": 16, "weights_rows": 48, "study_count": 5, "gene_count": 8,
            "genes": ["…"], "categories": ["…"]},
  "compilation": {"compile_success": true, "compiled_by": "marketplace-server",
                  "compiler_version": "just-dna-compiler 0.1.0",
                  "ensembl_reference": "just-dna-seq/ensembl_variations",
                  "compiled_at": "…", "warnings": []},
  "inputs":  [{"name": "variants.csv", "sha256": "sha256:…", "size": 4350}],
  "artifact": {"digest": "sha256:…",
               "files": [{"name": "weights.parquet", "sha256": "sha256:…", "size": 40190}]},
  "logs":    [{"name": "v2.log", "sha256": "sha256:…", "size": 1059987}]
}
```

`artifact.digest` is a Merkle root over `artifact.files` (the version's immutable content identity);
`inputs` and `logs` are hashed the same way but **not** part of that digest. All hashes are SHA-256,
lowercase hex, `sha256:`-prefixed. A downloader verifies with `just_dna_format.verify_manifest`
(see [CLIENT.md](CLIENT.md) / SPEC §5).
