"""
Catalog service: reads the projection and builds API models (cards, detail, versions, manifest).
Card stats are pulled from each module's latest-version manifest (the source of truth).
"""

import sqlite3
from typing import Optional

from just_dna_format.manifest import ModuleManifest

from just_dna_marketplace.config import API_PREFIX
from just_dna_marketplace.db.repository import Repository
from just_dna_marketplace.models.api import (
    CardStats,
    ModuleCard,
    ModuleDetail,
    Page,
    VersionSummary,
)

_CARD_GENES: int = 3  # genes shown on a card; the full list lives in the manifest


def _manifest_url(namespace: str, name: str, version: str) -> str:
    return f"{API_PREFIX}/modules/{namespace}/{name}/versions/{version}/manifest"


def _logo_url(namespace: str, name: str, version: str, manifest: Optional[ModuleManifest]) -> Optional[str]:
    if manifest is None or manifest.logo is None:
        return None
    return f"{API_PREFIX}/modules/{namespace}/{name}/versions/{version}/files/{manifest.logo.name}"


def _latest_manifest(repo: Repository, row: sqlite3.Row) -> Optional[ModuleManifest]:
    if not row["latest_version"]:
        return None
    raw = repo.get_manifest_json(row["namespace"], row["name"], row["latest_version"])
    return ModuleManifest.model_validate_json(raw) if raw else None


def _featured(repo: Repository, row: sqlite3.Row) -> bool:
    if "featured" in row.keys():  # search rows carry it (no extra query)
        return bool(row["featured"])
    flags = repo.namespace_flags(row["namespace"])
    return bool(flags["featured"]) if flags else False


def _card(repo: Repository, row: sqlite3.Row) -> ModuleCard:
    manifest = _latest_manifest(repo, row)
    stats = manifest.stats if manifest else None
    card_stats = CardStats(
        variant_count=stats.variant_count if stats else 0,
        study_count=stats.study_count if stats else 0,
        gene_count=stats.gene_count if stats else 0,
        genes=stats.genes[:_CARD_GENES] if stats else [],
        categories=stats.categories if stats else [],
        clinvar_count=stats.clinvar_count if stats else 0,
        pathogenic_count=stats.pathogenic_count if stats else 0,
        benign_count=stats.benign_count if stats else 0,
    )
    return ModuleCard(
        namespace=row["namespace"],
        name=row["name"],
        title=row["title"],
        description=row["description"],
        icon=manifest.display.icon if manifest else row["icon"],
        icon_set=manifest.display.icon_set if manifest else "fomantic",
        color=row["color"],
        logo_url=_logo_url(row["namespace"], row["name"], row["latest_version"], manifest)
        if row["latest_version"]
        else None,
        latest_version=row["latest_version"],
        genome_build=row["genome_build"],
        license=row["license"],
        owner=row["owner"],
        stats=card_stats,
        downloads=row["downloads"],
        updated_at=row["updated_at"],
        featured=_featured(repo, row),
    )


def _version_summary(row: sqlite3.Row, namespace: str, name: str) -> VersionSummary:
    return VersionSummary(
        version=row["version"],
        artifact_digest=row["digest"],
        compile_success=bool(row["compile_success"]),
        yanked=bool(row["yanked"]),
        signed=_version_signed(row),
        needs_upgrade=bool(row["needs_upgrade"]) if "needs_upgrade" in row.keys() else False,
        created_at=row["created_at"],
        changelog=row["changelog"],
        manifest_url=_manifest_url(namespace, name, row["version"]),
    )


def _version_signed(row: sqlite3.Row) -> bool:
    """Whether a version's stored manifest carries a signature (projected from manifest_json)."""
    if "manifest_json" not in row.keys() or not row["manifest_json"]:
        return False
    manifest = ModuleManifest.model_validate_json(row["manifest_json"])
    return manifest.signature is not None


def list_modules(
    repo: Repository, *, page: int, per_page: int, **filters: object
) -> Page[ModuleCard]:
    rows, total = repo.search_modules(
        limit=per_page, offset=(page - 1) * per_page, **filters  # type: ignore[arg-type]
    )
    return Page[ModuleCard](
        items=[_card(repo, r) for r in rows], total=total, page=page, per_page=per_page
    )


def module_detail(repo: Repository, namespace: str, name: str) -> Optional[ModuleDetail]:
    row = repo.get_module_row(namespace, name)
    if row is None:
        return None
    card = _card(repo, row)
    versions = repo.get_versions(row["id"])
    manifest = _latest_manifest(repo, row)
    data = card.model_dump()
    if manifest is not None:
        # Detail carries the FULL gene list (SPEC §8.3); only cards truncate.
        data["stats"] = manifest.stats.model_dump(include=set(CardStats.model_fields))
    return ModuleDetail(
        **data,
        readme=row["readme"],
        versions=[_version_summary(v, namespace, name) for v in versions],
        latest_manifest=manifest,
    )


def version_page(
    repo: Repository, namespace: str, name: str, *, page: int, per_page: int
) -> Optional[Page[VersionSummary]]:
    row = repo.get_module_row(namespace, name)
    if row is None:
        return None
    all_versions = repo.get_versions(row["id"])
    start = (page - 1) * per_page
    window = all_versions[start : start + per_page]
    return Page[VersionSummary](
        items=[_version_summary(v, namespace, name) for v in window],
        total=len(all_versions),
        page=page,
        per_page=per_page,
    )


def get_manifest(
    repo: Repository, namespace: str, name: str, version: str
) -> Optional[ModuleManifest]:
    raw = repo.get_manifest_json(namespace, name, version)
    return ModuleManifest.model_validate_json(raw) if raw else None
