"""
API request/response models (SPEC §8). Distinct from the `ModuleManifest` contract: these are the
catalog's card/detail/version shapes, projected from stored manifests.
"""

from typing import Generic, Optional, TypeVar

from just_dna_format.manifest import ModuleManifest
from pydantic import BaseModel, Field

T = TypeVar("T")


class CardStats(BaseModel):
    """Stats shown on a module card (genes truncated; full list lives in the manifest)."""

    variant_count: int = 0
    study_count: int = 0
    gene_count: int = 0
    genes: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)


class ModuleCard(BaseModel):
    """One entry in the list/search grid (SPEC §8.2)."""

    namespace: str
    name: str
    title: str
    description: str
    icon: str
    color: str
    latest_version: Optional[str]
    genome_build: str
    license: Optional[str]
    owner: Optional[str]
    stats: CardStats
    downloads: int
    updated_at: str
    featured: bool = False


class VersionSummary(BaseModel):
    """One entry in a version list (SPEC §8.4)."""

    version: str
    artifact_digest: str
    compile_success: bool
    yanked: bool
    created_at: str
    changelog: str
    manifest_url: str


class ModuleDetail(ModuleCard):
    """Module detail: card + readme + full versions + inline latest manifest (SPEC §8.3)."""

    readme: str
    versions: list[VersionSummary]
    latest_manifest: Optional[ModuleManifest]


class Page(BaseModel, Generic[T]):
    """Paginated envelope: `{items, total, page, per_page}`."""

    items: list[T]
    total: int
    page: int
    per_page: int


class WhoAmI(BaseModel):
    """Identity response for `GET /auth/whoami`."""

    account: str
    namespaces: list[str]
