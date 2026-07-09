"""
API request/response models (SPEC §8). Distinct from the `ModuleManifest` contract: these are the
catalog's card/detail/version shapes, projected from stored manifests.
"""

import re
from typing import Generic, Optional, TypeVar

from just_dna_format.manifest import ModuleManifest
from pydantic import BaseModel, Field, field_validator

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
    review_count: int = 0
    avg_rating: Optional[float] = None  # mean 1-5 rating across reviews, None when unreviewed
    curated: bool = False  # has ≥1 owner-highlighted review/audit (the `curated` group)


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
    """Identity response for `GET /auth/whoami`. `email` is private — returned only here, to the
    account itself, never in public listings."""

    account: str  # the unique handle (used in URLs and as reviewer attribution)
    namespaces: list[str]
    type: str = "user"  # GitHub-style discriminator: `user` | `org`
    display_name: Optional[str] = None
    email: Optional[str] = None


# Account identity vocab + a light email check (kept regex-based to avoid an email-validator dep).
VALID_ACCOUNT_TYPES: frozenset[str] = frozenset({"user", "org"})
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ProfileUpdate(BaseModel):
    """Body for `PATCH /auth/whoami` — the account edits its own profile. Omitted fields are left
    unchanged; an empty string clears a field. `type` is not self-editable (admin/creation-time)."""

    email: Optional[str] = None
    display_name: Optional[str] = None

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":  # "" clears the field
            return v
        if not _EMAIL_RE.match(v):
            raise ValueError("email must look like name@host.tld")
        return v


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


# Optional audit tier on a review (a correctness attestation about the reviewed version). A plain
# review omits it and is just a rating + notes.
VALID_VERDICTS: frozenset[str] = frozenset({"verified", "concerns", "rejected"})


class ReviewRequest(BaseModel):
    """Body for posting a review/audit of a version — a 1-5 rating plus an optional audit verdict."""

    rating: int = Field(ge=1, le=5, description="Overall rating, 1-5")
    verdict: Optional[str] = Field(
        default=None, description=f"Optional audit tier, one of {sorted(VALID_VERDICTS)}"
    )
    notes: Optional[str] = Field(default=None, description="Free-text review/audit notes")

    @field_validator("verdict")
    @classmethod
    def _validate_verdict(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_VERDICTS:
            raise ValueError(f"verdict must be one of {sorted(VALID_VERDICTS)}")
        return v


class Review(BaseModel):
    """A published review/audit of a specific version."""

    reviewer: str = Field(description="Reviewer account name")
    version: str
    rating: int
    verdict: Optional[str] = None
    notes: Optional[str] = None
    highlighted: bool = False  # the namespace owner accepted/highlighted this review
    created_at: str
    updated_at: str


class AddMemberRequest(BaseModel):
    """Body for `POST /namespaces/{ns}/members`."""

    account: str
    role: str = "contributor"
