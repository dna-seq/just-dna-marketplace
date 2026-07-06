"""Namespace availability + self-service claim (community onboarding, 0.3)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from just_dna_format.identity import is_valid_namespace
from pydantic import BaseModel

from just_dna_marketplace.api.deps import (
    Account,
    get_repo,
    require_account,
    settings_dep,
)
from just_dna_marketplace.config import Settings
from just_dna_marketplace.db.repository import Repository

router = APIRouter(prefix="/namespaces", tags=["namespaces"])

RepoDep = Annotated[Repository, Depends(get_repo)]
SettingsDep = Annotated[Settings, Depends(settings_dep)]
AccountDep = Annotated[Account, Depends(require_account)]


class ClaimRequest(BaseModel):
    namespace: str


@router.get("/{namespace}")
def availability(repo: RepoDep, namespace: str) -> dict:
    """Whether a namespace is free to claim (public)."""
    return {
        "namespace": namespace,
        "valid": is_valid_namespace(namespace),
        "available": repo.namespace_owner(namespace) is None,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def claim(repo: RepoDep, settings: SettingsDep, account: AccountDep, body: ClaimRequest) -> dict:
    """Claim an available namespace for the caller's account (up to `namespaces_per_account`)."""
    namespace = body.namespace
    if not is_valid_namespace(namespace):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid_namespace")

    owner = repo.namespace_owner(namespace)
    if owner is not None:
        if int(owner["account_id"]) == account.id:
            return {"namespace": namespace, "owner": account.name, "already_owned": True}
        raise HTTPException(status.HTTP_409_CONFLICT, detail="namespace_taken")

    if repo.count_namespaces_for_account(account.id) >= settings.namespaces_per_account:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="namespace_limit_reached")

    repo.add_namespace(namespace, account.id)
    return {"namespace": namespace, "owner": account.name, "already_owned": False}
