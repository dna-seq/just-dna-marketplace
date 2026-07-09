"""
Reviews / audits (0.8.0) — registry-layer social data about a published version, never part of
the immutable module manifest.

Anyone authenticated posts one review per version (a 1-5 rating + optional audit verdict + notes);
the namespace **owner** highlights the good ones (SO accepted-answer style), and a highlighted review
is what the `curated` listing group keys on. Mirrors the stars surface: mutate → return the current
review set.
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from just_dna_registry.api.deps import (
    Account,
    get_repo,
    optional_account,
    rate_limit,
    require_account,
    require_namespace_owner,
)
from just_dna_registry.db.repository import Repository
from just_dna_registry.models.api import Review, ReviewRequest
from just_dna_registry.services.ingest import now_iso

router = APIRouter(prefix="/modules", tags=["reviews"])

RepoDep = Annotated[Repository, Depends(get_repo)]
AccountDep = Annotated[Account, Depends(require_account)]
CallerDep = Annotated[Optional[Account], Depends(optional_account)]


def _module_id(repo: Repository, namespace: str, name: str) -> int:
    row = repo.get_module_row(namespace, name)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="module_not_found")
    return int(row["id"])


def _require_version(repo: Repository, namespace: str, name: str, version: str) -> int:
    if not repo.version_exists(namespace, name, version):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="version_not_found")
    return _module_id(repo, namespace, name)


def _reviews(repo: Repository, module_id: int, version: Optional[str]) -> list[Review]:
    return [
        Review(
            reviewer=r["reviewer"],
            version=r["version"],
            rating=int(r["rating"]),
            verdict=r["verdict"],
            notes=r["notes"],
            highlighted=bool(r["highlighted"]),
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in repo.list_reviews(module_id, version)
    ]


@router.get("/{namespace}/{name}/reviews", response_model=list[Review])
def list_module_reviews(repo: RepoDep, namespace: str, name: str) -> list[Review]:
    """Every review of a module, across versions (highlighted first). Anonymous."""
    return _reviews(repo, _module_id(repo, namespace, name), None)


@router.get("/{namespace}/{name}/versions/{version}/reviews", response_model=list[Review])
def list_version_reviews(
    repo: RepoDep, namespace: str, name: str, version: str
) -> list[Review]:
    """Reviews of one version (highlighted first). Anonymous."""
    return _reviews(repo, _require_version(repo, namespace, name, version), version)


@router.put(
    "/{namespace}/{name}/versions/{version}/reviews",
    response_model=list[Review],
    dependencies=[Depends(rate_limit("social"))],
)
def upsert_review(
    repo: RepoDep, account: AccountDep, namespace: str, name: str, version: str, body: ReviewRequest
) -> list[Review]:
    """Post or update the caller's review/audit of a version. Anyone authenticated; one per account
    per version (re-posting replaces it). Does not touch the owner's highlight."""
    module_id = _require_version(repo, namespace, name, version)
    repo.upsert_review(
        module_id, version, account.id,
        rating=body.rating, verdict=body.verdict, notes=body.notes, now=now_iso(),
    )
    return _reviews(repo, module_id, version)


@router.delete(
    "/{namespace}/{name}/versions/{version}/reviews",
    response_model=list[Review],
    dependencies=[Depends(rate_limit("social"))],
)
def delete_review(
    repo: RepoDep, account: AccountDep, namespace: str, name: str, version: str
) -> list[Review]:
    """Remove the caller's own review of a version. Idempotent."""
    module_id = _require_version(repo, namespace, name, version)
    repo.delete_review(module_id, version, account.id)
    return _reviews(repo, module_id, version)


@router.put(
    "/{namespace}/{name}/versions/{version}/reviews/{reviewer}/highlight",
    response_model=list[Review],
)
def highlight_review(
    repo: RepoDep, account: AccountDep, namespace: str, name: str, version: str, reviewer: str
) -> list[Review]:
    """Highlight a reviewer's review (SO accepted-answer style) — the signal the `curated` tab keys
    on. Namespace **owner** only. `the more the merrier`: any number may be highlighted."""
    require_namespace_owner(repo, account, namespace)
    module_id = _require_version(repo, namespace, name, version)
    target = repo.account_by_name(reviewer)
    if target is None or not repo.set_review_highlight(module_id, version, int(target["id"]), True):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="review_not_found")
    return _reviews(repo, module_id, version)


@router.delete(
    "/{namespace}/{name}/versions/{version}/reviews/{reviewer}/highlight",
    response_model=list[Review],
)
def unhighlight_review(
    repo: RepoDep, account: AccountDep, namespace: str, name: str, version: str, reviewer: str
) -> list[Review]:
    """Remove the owner's highlight from a reviewer's review. Namespace owner only. Idempotent."""
    require_namespace_owner(repo, account, namespace)
    module_id = _require_version(repo, namespace, name, version)
    target = repo.account_by_name(reviewer)
    if target is not None:
        repo.set_review_highlight(module_id, version, int(target["id"]), False)
    return _reviews(repo, module_id, version)
