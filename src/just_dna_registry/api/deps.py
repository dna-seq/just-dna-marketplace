"""
FastAPI dependencies: DB/storage accessors, pagination, and static-API-key auth.

Auth is MVP-simple (SPEC decision): a pre-issued API key in `Authorization: Bearer <key>`
resolves to an account; publishing under a namespace requires that account to own it.
"""

from dataclasses import dataclass
from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, Query, Request, status

from just_dna_registry.config import Settings
from just_dna_registry.db.repository import Repository
from just_dna_registry.jwtauth import decode_jwt
from just_dna_registry.permissions import Capability, OWN_FALLBACK, higher_role, role_has
from just_dna_registry.storage.base import StorageBackend


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
    settings: Annotated[Settings, Depends(settings_dep)],
    authorization: Annotated[Optional[str], Header()] = None,
) -> Account:
    """Resolve the bearer credential to an account, or 401.

    Accepts a static API key (tried first — unchanged behaviour) or, when JWT is enabled, a JWT
    session token minted by `POST /auth/tokens`.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="missing_bearer_token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()

    row = repo.account_for_key(token)  # static API key
    if row is not None:
        account_id = int(row["id"])
        return Account(account_id, row["name"], repo.namespaces_for_account(account_id))

    claims = decode_jwt(settings, token)  # optional JWT session
    if claims is not None:
        account_id = int(claims["account_id"])
        return Account(account_id, claims["sub"], repo.namespaces_for_account(account_id))

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid_token")


def optional_account(
    repo: Annotated[Repository, Depends(get_repo)],
    settings: Annotated[Settings, Depends(settings_dep)],
    authorization: Annotated[Optional[str], Header()] = None,
) -> Optional[Account]:
    """Resolve the bearer credential to an account when present and valid; otherwise None.

    For otherwise-anonymous reads that want to personalise a response (e.g. `starred_by_me`) without
    forcing authentication. A malformed/expired token yields None rather than a 401.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    row = repo.account_for_key(token)
    if row is not None:
        account_id = int(row["id"])
        return Account(account_id, row["name"], repo.namespaces_for_account(account_id))
    claims = decode_jwt(settings, token)
    if claims is not None:
        account_id = int(claims["account_id"])
        return Account(account_id, claims["sub"], repo.namespaces_for_account(account_id))
    return None


def effective_role(repo: Repository, account: Account, namespace: str) -> Optional[str]:
    """The caller's effective role on a namespace: the highest of their explicit per-namespace grant
    and — when the namespace is owned by an **org** they belong to — their org role (the cascade).
    This is the single place the two ownership sources are reconciled."""
    per_ns = repo.namespace_role(namespace, account.id)
    org_role: Optional[str] = None
    owner_row = repo.namespace_owner(namespace)
    if owner_row is not None:
        owning_id = int(owner_row["account_id"])
        if repo.account_type(owning_id) == "org":
            org_role = repo.org_role(owning_id, account.id)
    return higher_role(per_ns, org_role)


def require_capability(
    repo: Repository,
    account: Account,
    namespace: str,
    cap: Capability,
    *,
    resource_author: Optional[int] = None,
) -> None:
    """Raise 403 unless `account` has `cap` on `namespace`. For an `*_ANY` capability, auto-downgrade
    to the matching `*_OWN` variant when the caller authored the resource (`resource_author`)."""
    role = effective_role(repo, account, namespace)
    if role_has(role, cap):
        return
    own = OWN_FALLBACK.get(cap)
    if (
        own is not None
        and resource_author is not None
        and resource_author == account.id
        and role_has(role, own)
    ):
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="insufficient_capability")


def require_org_capability(
    repo: Repository, account: Account, org_id: int, cap: Capability
) -> None:
    """Raise 403 unless `account` has `cap` in the org (`org_members` role; no cascade at org level)."""
    if not role_has(repo.org_role(org_id, account.id), cap):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="insufficient_capability")


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
