# Marketplace page — deliverable for the just-dna-lite webui

What this repo hands the webui team to build a **Marketplace** page on, and how the pieces connect.
The heavy lifting (REST calls + integrity verification + provenance) is the reference client;
the webui builds the Reflex components on top.

## What's provided here (build on these)

- **`just_dna_marketplace.MarketplaceClient`** — the light client (deps: `httpx` + `just-dna-format`;
  `pip install just-dna-marketplace`, no server extra). Every page action maps to one method — see
  [CLIENT.md](CLIENT.md).
- **Stable response shapes** — cards, detail, versions, manifest — in [API-REFERENCE.md](API-REFERENCE.md).
- **Provenance primitives** — `manifest.json` fields + `lookup_by_digests` (no spec change needed).
- **Onboarding** — `register` (install-id) + `claim_namespace`, so publishing never leaves the app.

The webui only adds: Reflex state + components, and the install/publish glue to existing lite paths
(`register_custom_module`, `refresh_module_registry`, `CUSTOM_MODULES_DIR`).

## Page anatomy → client calls

| UI element | Client call | Notes |
|---|---|---|
| **Catalog grid** (cards) | `list_modules(q=, gene=, category=, genome_build=, owner=, license=, sort=, featured=, page=, per_page=)` | Card = `{namespace, name, title, description, icon, color, latest_version, stats, downloads, updated_at, featured}`. `featured` cards float to top → show a badge. Paginate on `total`. |
| **Search / facet filters** | same, with query params | `sort` ∈ `name\|downloads\|recent`. Facets: `gene`, `category`, `genome_build`, `owner`, `license`. |
| **Detail drawer** | `get_module(ns, name)` | Adds `readme` (MODULE.md), **full** `stats.genes`, `versions[]`, and inline `latest_manifest`. |
| **Version picker** | `versions(ns, name)` or `detail.versions` | Each `VersionSummary` has `compile_success`, `yanked`, `changelog`, `manifest_url`. |
| **Install** | `download(ns, name, version, CUSTOM_MODULES_DIR/name)` | Fetches artifact (+logs), **verifies integrity** (raises `IntegrityError`), writes `manifest.json`. Then call the existing `register_custom_module` + `refresh_module_registry`. |
| **Publish current module** | `publish(ns, name, version, spec_dir)` or `import_module(...)` | Needs a token (see onboarding). `publish` stamps the returned manifest back into the local spec dir. |
| **"Already published?" / provenance** | `lookup_by_digests([...])` | Batch-classify local modules in one call (digests come from each local `manifest.json`). |

## Provenance (badge modules without touching `module_spec.yaml`)

Read the local module's `manifest.json`:

- `compilation.compiled_by == "marketplace-server"` and `identity.namespace` set → **Installed from
  marketplace** → show `identity.canonical_id`, offer "check for updates" (compare `latest_version`).
- null `identity` → **Custom (local)**. To flag "published by me", `lookup_by_digests` the local
  `artifact.digest` (or rely on the publish-time stamp the client writes back).

## Onboarding (publish without leaving the app)

1. First run: mint an install-id — `from just_dna_marketplace import generate_install_id`
   (proof-of-work, a few seconds; persist it).
2. `client.register(install_id, account)` → `{token, account, namespaces}`; store the token.
3. `client.namespace_available(ns)` → `client.claim_namespace(ns)` (up to 5 per account).
4. `client.publish(...)` / `import_module(...)`.

## Suggested Reflex state (sketch)

```python
class MarketplaceState(rx.State):
    query: str = ""; sort: str = "name"; page: int = 1
    cards: list[dict] = []; total: int = 0
    selected: dict | None = None          # detail
    token: str = ""; install_id: str = "" # onboarding (persisted)

    def _client(self):
        return MarketplaceClient(MARKETPLACE_URL, self.token or None)

    @rx.event(background=True)   # network → keep off the UI lock (see AGENTS.md Reflex rules)
    async def search(self):
        with self._client() as c:
            body = c.list_modules(q=self.query or None, sort=self.sort, page=self.page)
        async with self:
            self.cards, self.total = body["items"], body["total"]
```

Config: the webui needs `MARKETPLACE_URL` (default `https://module-marketplace.just-dna.life`); the
token comes from onboarding, stored per user. Run network calls in `@rx.event(background=True)` so
they don't hold the Reflex state lock (per just-dna-lite's Reflex guidance).

## Not in scope for the webui build

Rate limiting, moderation (featured/blacklisted), key issuance/revocation, and storage are all
server-side — the page just consumes the API. Blacklisted namespaces are already hidden from
`list_modules` by default (opt in with `include_blacklisted=true` only for admin views).
