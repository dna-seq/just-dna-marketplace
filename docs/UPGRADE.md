# Contract upgrades & the stale-module procedure

`just-dna-marketplace` pins a `just-dna-format` / `just-dna-compiler` contract version. Bumping it
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
2. **Audit with `marketplace revalidate`.** It re-runs the *current* `validate_spec` over every
   published version's stored spec inputs and reports `ok` / `needs_upgrade` / `skipped` (spec inputs
   not retrievable). Published artifacts are never touched.
   - `--set-flag` persists a non-destructive `needs_upgrade` flag on failing versions (surfaced in
     the versions API as `needs_upgrade: true`). The version stays fetchable and keeps verifying.
   - `--check-pmids` additionally verifies each study PMID resolves at NCBI E-utilities (the online
     "curl validator"). This is a **marketplace ops** call — the contract libs stay strictly offline;
     `just-dna-format` only does the cheap regex (`extract_pmids`).
3. **Upgrade a flagged version** by re-publishing, never mutating old bytes:
   - Apply a migration transform to the spec inputs (for PMID: `extract_pmids` → digit-only; drop or
     fix references that don't resolve online).
   - `marketplace-client publish … <new PATCH version>` — a normal publish under the new contract.
   - The predecessor stays fetchable (existing installs keep working); yank it once the successor is
     live if you want it out of `latest`/listings.
4. **Out-of-digest assets never trigger this.** `logs`, `provenance`, and `logo` are hashed but
   excluded from `artifact.digest`; a logo change is a PATCH via `amend-logo`, not a re-publish.

## Boundary: where the network lives

The contract libraries (`just-dna-format`, `just-dna-compiler`) are a stated no-network zone. So:

- **Regex validation** (does the string carry a PMID token?) → `just-dna-format` (`PMID_PATTERN` /
  `extract_pmids`).
- **Existence verification** (does the PMID resolve at NCBI?) → marketplace ops
  (`services/pmid_check.verify_pmids`, reached via `revalidate --check-pmids`) or the authoring tier.
