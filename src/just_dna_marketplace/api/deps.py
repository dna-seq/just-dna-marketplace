"""
FastAPI dependencies: DB/storage accessors, pagination, and static-API-key auth.

Auth is MVP-simple (SPEC decision): a pre-issued API key in `Authorization: Bearer <key>`
resolves to an account; publishing under a namespace requires that account to own it.
"""

from dataclasses import dataclass
from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, Query, Request, status

from just_dna_marketplace.config import Settings
from just_dna_marketplace.db.repository import Repository
from just_dna_marketplace.storage.base import StorageBackend


def get_repo(request: Request) -> Repository:
    return request.app.state.repo


def get_storage(request: Request) -> StorageBackend:
    return request.app.state.storage


def settings_dep(request: Request) -> Settings:
    return request.app.state.settings


@dataclass(frozen=True)
class Account:
    """The authenticated caller."""

    id: int
    name: str
    namespaces: list[str]


@dataclass(frozen=True)
class Pagination:
    page: int
    per_page: int


def pagination(
    settings: Annotated[Settings, Depends(settings_dep)],
    page: int = Query(1, ge=1),
    per_page: Optional[int] = Query(None, ge=1),
) -> Pagination:
    """Clamp `per_page` to the configured maximum; default when unset."""
    resolved = per_page or settings.default_per_page
    return Pagination(page=page, per_page=min(resolved, settings.max_per_page))


def require_account(
    repo: Annotated[Repository, Depends(get_repo)],
    authorization: Annotated[Optional[str], Header()] = None,
) -> Account:
    """Resolve the bearer API key to an account, or 401."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="missing_bearer_token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    key = authorization.split(" ", 1)[1].strip()
    row = repo.account_for_key(key)
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid_token")
    account_id = int(row["id"])
    return Account(
        id=account_id,
        name=row["name"],
        namespaces=repo.namespaces_for_account(account_id),
    )


def require_namespace_member(account: Account, namespace: str) -> None:
    """Raise 403 unless `account` owns `namespace`."""
    if namespace not in account.namespaces:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not_namespace_member")


def _rate_identity(request: Request) -> str:
    """Rate-limit key: the API key if present (first 16 chars), else the client IP."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return "key:" + auth.split(" ", 1)[1].strip()[:16]
    return "ip:" + (request.client.host if request.client else "unknown")


def rate_limit(category: str):
    """Dependency factory: enforce the token bucket for `category` (429 on exhaustion)."""

    def _dep(request: Request) -> None:
        limiter = request.app.state.rate_limiter
        if not limiter.allow(_rate_identity(request), category):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, detail="rate_limited",
                headers={"Retry-After": "60"},
            )

    return _dep
