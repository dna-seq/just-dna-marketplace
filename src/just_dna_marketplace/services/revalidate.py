"""
Contract-drift audit: re-run the *current* `validate_spec` over a published version's stored spec
inputs to find modules that no longer satisfy the contract after a `just-dna-format` bump.

Published artifacts are immutable and keep verifying by `artifact.digest`; this never touches them.
It only tells you which versions would fail a re-compile today, so the `needs_upgrade` flag can be
set and an upgrade (re-publish as a new PATCH) scheduled. See docs/UPGRADE.md.
"""

import csv
import io
import tempfile
from pathlib import Path

from just_dna_compiler.compiler import validate_spec
from just_dna_format.manifest import ModuleManifest
from just_dna_format.spec import extract_pmids

from just_dna_marketplace.services.upgrade import plan_variants_upgrade
from just_dna_marketplace.storage.base import StorageBackend, version_key

# The spec inputs `validate_spec` needs; other inputs (MODULE.md, logo) are irrelevant to it.
_SPEC_INPUTS: tuple[str, ...] = ("module_spec.yaml", "variants.csv", "studies.csv")


def revalidate_version(
    storage: StorageBackend, namespace: str, name: str, version: str, manifest: ModuleManifest
) -> tuple[str, list[str]]:
    """Re-run the current `validate_spec` against a version's stored spec inputs.

    Returns `(status, messages)` where status is:
      * `"needs_upgrade"` — the spec no longer *validates* under the current contract (a tightened
        rule, e.g. the 0.2 PMID pattern). Re-publish is required.
      * `"upgradable"` — the spec still validates, but one or more variant rows can be losslessly
        back-populated to the additive 0.3 columns (direction/stat_significance/clin_sig) from the
        legacy `state`/booleans. Re-publish is optional-but-recommended; run `marketplace upgrade`.
      * `"ok"` — validates and already carries the current columns.
      * `"skipped"` — spec inputs aren't retrievable (e.g. a legacy import that shipped no inputs;
        not counted as a failure).
    """
    key = version_key(namespace, name, version)
    # Base on what's actually retrievable from storage (not just what the manifest lists), so a
    # missing artifact can't masquerade as a contract failure.
    present = [
        n for n in _SPEC_INPUTS
        if any(e.name == n for e in manifest.inputs) and storage.exists(key, n)
    ]
    if "module_spec.yaml" not in present or "variants.csv" not in present:
        return "skipped", ["spec inputs not available for revalidation"]
    with tempfile.TemporaryDirectory() as tmp:
        spec = Path(tmp)
        for iname in present:
            (spec / iname).write_bytes(storage.read_file(key, iname))
        result = validate_spec(spec)
        if not result.valid:
            return "needs_upgrade", result.errors
        # Still valid — but do the additive 0.3 axes have a legacy source to back-populate?
        plan = plan_variants_upgrade((spec / "variants.csv").read_text(encoding="utf-8"))
    if plan.needed:
        return "upgradable", [
            f"{plan.upgradable_rows}/{plan.total_rows} variant row(s) can be back-populated to the "
            f"0.3 columns (direction/stat_significance/clin_sig) — run `marketplace upgrade`"
        ]
    return "ok", []


def gather_pmids(
    storage: StorageBackend, namespace: str, name: str, version: str, manifest: ModuleManifest
) -> list[str]:
    """All digit-only PMIDs referenced by a version's `studies.csv` (deduplicated, in order)."""
    key = version_key(namespace, name, version)
    if not any(e.name == "studies.csv" for e in manifest.inputs) or not storage.exists(key, "studies.csv"):
        return []
    text = storage.read_file(key, "studies.csv").decode("utf-8")
    seen: dict[str, None] = {}
    for row in csv.DictReader(io.StringIO(text)):
        for pmid in extract_pmids((row.get("pmid") or "").strip()):
            seen.setdefault(pmid, None)
    return list(seen)
