# Contract upgrades & the stale-module procedure

## 0.9.0 RBAC migration (operator note)

0.9.0 renamed the namespace role `contributor` → `member` (migrated in place by `init_db`) and made
authorization capability-based. **This tightens permissions:** an old `contributor` could
amend/yank *any* version in the namespace; a `member` can only amend/yank versions **it published**
(`versions.published_by`). To restore broad rights for someone, grant them `admin`
(`registry add-member <ns> <account> --role admin`). Also: versions published **before** 0.9.0 have
no recorded author, so only `admin`+ can amend/yank them (fail-closed). Orgs (`type='org'`) now have
members whose role cascades to org-owned namespaces — see the org endpoints/CLI. No artifact or
manifest is affected; this is a DB-projection + authorization change only.

---

`just-dna-registry` pins a `just-dna-format` / `just-dna-compiler` contract version. Bumping it
can tighten a validator (the archetype: `StudyRow.pmid` gaining a `PMID_PATTERN` rule in 0.2.0).
This doc is the agreed procedure for such events, so a contract bump can never silently strand
published modules.

## What is and isn't at risk

A contract bump does **not** break anything already deployed:

- **Published manifests are immutable and stay valid.** They were compiled under the contract in
  force at publish time; `artifact.digest` is unchanged; existing installs keep verifying by digest.
- **Every version self-declares its contract** — `compilation.compiler_version` (e.g.
  `"just-dna-compiler 0.2.0"`) and `schema_version` are in each manifest.
- **Spec inputs are retained** — `module_spec.yaml` / `variants.csv` / `studies.csv` are stored as
  `inputs[]`, so any version can be re-validated on demand.

What *is* at risk: a **re-compile / re-publish** of an old spec under the new server, and **catalog
truth** — knowing which published modules would fail *today's* contract.

## The mechanism

1. **Prefer additive, verbatim-preserving changes.** 0.2.0's PMID rule keeps the original string and
   only rejects references with no PMID token at all (e.g. a bare dbSNP URL, which grounds zero
   studies anyway). Audited against the Gen-I corpus → nothing published was invalidated.
2. **Audit with `registry revalidate`.** It re-runs the *current* `validate_spec` over every
   published version's stored spec inputs and reports `ok` / `upgradable` / `needs_upgrade` /
   `skipped` (spec inputs not retrievable). Published artifacts are never touched.
   - `needs_upgrade` — the spec no longer **validates** (a tightened rule, e.g. the 0.2 PMID pattern).
   - `upgradable` — the spec still validates, but one or more variant rows can be **losslessly
     back-populated** to an *additive* contract (the 0.3 `direction`/`stat_significance`/`clin_sig`
     axes, derived from the legacy `state`/booleans). Optional-but-recommended.
   - `--set-flag` persists a non-destructive `needs_upgrade` flag on both (surfaced in the versions
     API as `needs_upgrade: true`). The version stays fetchable and keeps verifying.
   - `--check-pmids` additionally verifies each study PMID resolves at NCBI E-utilities (the online
     "curl validator"). This is a **registry ops** call — the contract libs stay strictly offline;
     `just-dna-format` only does the cheap regex (`extract_pmids`).
3. **Upgrade a flagged version** by re-publishing, never mutating old bytes:
   - **Additive-column upgrades (0.3) are automated: `registry upgrade`.** It applies the format's
     own `VariantRow.upgraded()` derivation to the stored `variants.csv` (back-populate
     `direction`/`stat_significance`/`clin_sig`, trim `state` to its derived legacy mirror), then
     re-publishes as the next PATCH through the normal server-side compile path. Dry-run by default;
     `--apply` publishes. Scope with `-n`/`-m`. **Only a module's latest non-yanked version is
     upgraded** — the original is immutable and stays drifted, so an older version already superseded
     by a newer one is skipped (and `revalidate` reports it `superseded`, not `upgradable`).
     Idempotent: once the latest is on-contract, re-running does nothing (no endless patch chain).
   - **Validator-failure upgrades** stay a manual transform + publish: apply the fix to the spec
     inputs (for PMID: `extract_pmids` → digit-only; drop or fix references that don't resolve
     online), then `registry-client publish … <new PATCH version>` under the new contract.
   - Either way the predecessor stays fetchable (existing installs keep working); yank it once the
     successor is live if you want it out of `latest`/listings.
4. **Out-of-digest assets never trigger this.** `logs`, `provenance`, and `logo` are hashed but
   excluded from `artifact.digest`; a logo change is a PATCH via `amend-logo`, not a re-publish.

## Boundary: where the network lives

The contract libraries (`just-dna-format`, `just-dna-compiler`) are a stated no-network zone. So:

- **Regex validation** (does the string carry a PMID token?) → `just-dna-format` (`PMID_PATTERN` /
  `extract_pmids`).
- **Existence verification** (does the PMID resolve at NCBI?) → registry ops
  (`services/pmid_check.verify_pmids`, reached via `revalidate --check-pmids`) or the authoring tier.
