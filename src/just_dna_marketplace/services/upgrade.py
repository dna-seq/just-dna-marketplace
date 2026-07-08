"""
0.3 contract upgrade: back-populate the orthogonal 0.3 axes into a published version's spec and
re-publish as a new PATCH.

Unlike the `revalidate` audit (which finds versions the current validator *rejects*), the 0.3
columns are **additive** — a legacy module still validates. The drift here is *opportunistic*: a row
that carries only the legacy `state`/ClinVar booleans can be losslessly enriched with `direction`,
`stat_significance`, `clin_sig` (and a trimmed `state`) via the format's own
`VariantRow.upgraded()` derivation. This module automates docs/UPGRADE.md step 3 for that case:
migrate the stored `variants.csv`, then re-publish through the normal server-side compile path so
`compile_success`, hashes, and the digest are produced by the trusted party. Old bytes are never
mutated; the predecessor stays fetchable.

`studies.csv` is not migrated — `StudyRow` has no `state` to derive from (its new 0.3 columns have no
legacy source), so it passes through verbatim.
"""

import csv
import io
from typing import Optional

from just_dna_format.identity import parse_version
from just_dna_format.manifest import ModuleManifest
from just_dna_format.spec import VariantRow
from pydantic import BaseModel, Field

from just_dna_marketplace.config import Settings
from just_dna_marketplace.db.repository import Repository
from just_dna_marketplace.services.publish import REQUIRED_SPEC_FILES, publish_version
from just_dna_marketplace.storage.base import StorageBackend, version_key

# The columns `VariantRow.upgraded()` may set, mirrored back into the CSV. `state` stays present
# (trimmed to a derived mirror of `direction`); the booleans are only ever set True or left blank.
_UPGRADED_COLUMNS: tuple[str, ...] = (
    "state",
    "direction",
    "stat_significance",
    "clin_sig",
    "pathogenic",
    "benign",
)


class UpgradePlan(BaseModel):
    """What a 0.3 upgrade of a single `variants.csv` would do (computed, not yet applied)."""

    total_rows: int = Field(description="Variant rows in the spec")
    upgradable_rows: int = Field(description="Rows whose 0.3 axes can be back-populated")
    migrated_variants_csv: str = Field(description="The rewritten variants.csv (== input if none)")

    @property
    def needed(self) -> bool:
        return self.upgradable_rows > 0


def _csv_cell(value: object) -> str:
    """Serialize an upgraded field back to a CSV cell, matching the compiler's reverse writer:
    a True boolean becomes 'true'; None/False become '' (absent); strings pass through."""
    if value is None or value is False:
        return ""
    if value is True:
        return "true"
    return str(value)


def plan_variants_upgrade(variants_csv_text: str) -> UpgradePlan:
    """Compute the 0.3 back-population for a `variants.csv` string. Pure and idempotent: re-planning
    an already-upgraded CSV reports zero upgradable rows and returns it unchanged."""
    reader = csv.DictReader(io.StringIO(variants_csv_text))
    in_fields = list(reader.fieldnames or [])
    out_fields = in_fields + [c for c in _UPGRADED_COLUMNS if c not in in_fields]

    out_rows: list[dict[str, str]] = []
    upgradable = 0
    total = 0
    for raw in reader:
        total += 1
        # Mirror the compiler's CSV loader: blank cells are absent (None), everything else stripped.
        cleaned = {
            k: (v.strip() if isinstance(v, str) and v.strip() != "" else None)
            for k, v in raw.items()
            if k is not None
        }
        row = VariantRow.model_validate(cleaned)
        # Preserve the original cells verbatim; only touch the derived columns, and only when the row
        # actually drifts (so an already-0.3 row is left byte-identical).
        out = {k: (v if v is not None else "") for k, v in raw.items() if k is not None}
        if row.needs_upgrade:
            upgradable += 1
            up = row.upgraded()
            for col in _UPGRADED_COLUMNS:
                out[col] = _csv_cell(getattr(up, col))
        out_rows.append(out)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=out_fields, extrasaction="ignore", restval="")
    writer.writeheader()
    writer.writerows(out_rows)
    migrated = buf.getvalue() if total else variants_csv_text
    return UpgradePlan(total_rows=total, upgradable_rows=upgradable, migrated_variants_csv=migrated)


def plan_version_upgrade(
    storage: StorageBackend, namespace: str, name: str, version: str, manifest: ModuleManifest
) -> Optional[UpgradePlan]:
    """Plan the 0.3 upgrade of a published version from its stored `variants.csv`, or None when the
    spec inputs a re-publish needs aren't all retrievable (a legacy import — cannot be upgraded)."""
    key = version_key(namespace, name, version)
    have = {
        e.name for e in manifest.inputs if e.name in REQUIRED_SPEC_FILES and storage.exists(key, e.name)
    }
    if set(REQUIRED_SPEC_FILES) - have:
        return None
    variants = storage.read_file(key, "variants.csv").decode("utf-8")
    return plan_variants_upgrade(variants)


def _next_free_patch(repo: Repository, namespace: str, name: str, version: str) -> str:
    """The next PATCH after `version` not already taken (`1.0.0` → `1.0.1`, skipping any in use)."""
    v = parse_version(version)
    patch = v.patch + 1
    while repo.version_exists(namespace, name, f"{v.major}.{v.minor}.{patch}"):
        patch += 1
    return f"{v.major}.{v.minor}.{patch}"


def upgrade_version(
    *,
    repo: Repository,
    storage: StorageBackend,
    settings: Settings,
    namespace: str,
    name: str,
    version: str,
    manifest: ModuleManifest,
    changelog: Optional[str] = None,
) -> Optional[tuple[str, ModuleManifest]]:
    """Migrate a version's `variants.csv` to the 0.3 columns and re-publish as the next PATCH.

    Returns `(new_version, new_manifest)`, or None when nothing needs upgrading (or the spec inputs
    aren't retrievable). The re-publish runs the full server-side compile path, so the successor
    carries a freshly-computed, trusted digest; the predecessor is left untouched.
    """
    plan = plan_version_upgrade(storage, namespace, name, version, manifest)
    if plan is None or not plan.needed:
        return None

    key = version_key(namespace, name, version)
    # Carry the spec inputs (yaml/csv/MODULE.md) forward, plus the logo (version-independent
    # branding, out of the digest). Logs/provenance are intentionally NOT carried: they describe how
    # the *predecessor* was built, and this mechanical migration has its own (absent) provenance.
    carry = [e.name for e in manifest.inputs if not e.name.endswith(".parquet")]
    if manifest.logo is not None:
        carry.append(manifest.logo.name)
    files: dict[str, bytes] = {
        n: storage.read_file(key, n) for n in carry if storage.exists(key, n)
    }
    files["variants.csv"] = plan.migrated_variants_csv.encode("utf-8")

    new_version = _next_free_patch(repo, namespace, name, version)
    new_manifest = publish_version(
        repo=repo,
        storage=storage,
        settings=settings,
        namespace=namespace,
        name=name,
        version=new_version,
        changelog=changelog
        or (
            f"Automated 0.3 contract upgrade of {version}: back-populated "
            f"direction/stat_significance/clin_sig for {plan.upgradable_rows} variant row(s)."
        ),
        owner=manifest.owner or namespace,
        files=files,
    )
    return new_version, new_manifest
