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
    clinvar_count: int = 0
    pathogenic_count: int = 0
    benign_count: int = 0


class ModuleCard(BaseModel):
    """One entry in the list/search grid (SPEC §8.2)."""

    namespace: str
    name: str
    title: str
    description: str
    icon: str
    icon_set: str = "fomantic"
    color: str
    logo_url: Optional[str] = None  # served logo, when the module ships one; else fall back to icon
    latest_version: Optional[str]
    genome_build: str
    license: Optional[str]
    owner: Optional[str]
    stats: CardStats
    downloads: int
    stars: int = 0
    views: int = 0
    created_at: str = ""  # first-publish time (distinct from updated_at)
    updated_at: str
    starred_by_me: bool = False  # true when the authenticated caller has starred this module
    featured: bool = False


class VersionSummary(BaseModel):
    """One entry in a version list (SPEC §8.4)."""

    version: str
    artifact_digest: str
    compile_success: bool
    yanked: bool
    signed: bool = False  # carries an Ed25519 signature over artifact.digest (SPEC §5)
    needs_upgrade: bool = False  # set by the `revalidate` audit: fails the current contract
    downloads: int = 0  # per-version download count (0.6.0)
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


class MemberEntry(BaseModel):
    """One namespace member: an account and its role (`owner` | `contributor`)."""

    account: str
    role: str


class MemberList(BaseModel):
    """Members of a namespace (`GET /namespaces/{ns}/members`)."""

    namespace: str
    members: list[MemberEntry]


class StarStatus(BaseModel):
    """Star toggle result for a module (`PUT`/`DELETE .../star`)."""

    namespace: str
    name: str
    stars: int
    starred_by_me: bool


class AddMemberRequest(BaseModel):
    """Body for `POST /namespaces/{ns}/members`."""

    account: str
    role: str = "contributor"
