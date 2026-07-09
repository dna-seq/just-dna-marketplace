"""Namespace availability + self-service claim (community onboarding, 0.3)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from just_dna_format.identity import is_valid_namespace
from pydantic import BaseModel

from just_dna_registry.api.deps import (
    Account,
    get_repo,
    require_account,
    require_capability,
    settings_dep,
)
from just_dna_registry.config import Settings
from just_dna_registry.db.repository import Repository
from just_dna_registry.models.api import AddMemberRequest, MemberEntry, MemberList
from just_dna_registry.permissions import VALID_NS_ROLES, Capability

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


@router.get("/{namespace}/members", response_model=MemberList)
def list_members(repo: RepoDep, account: AccountDep, namespace: str) -> MemberList:
    """List a namespace's members. Any member may read the roster."""
    require_capability(repo, account, namespace, Capability.PUBLISH)  # any role has PUBLISH
    return MemberList(
        namespace=namespace,
        members=[MemberEntry(account=r["account"], role=r["role"]) for r in repo.list_members(namespace)],
    )


@router.post("/{namespace}/members", status_code=status.HTTP_201_CREATED, response_model=MemberList)
def add_member(
    repo: RepoDep, account: AccountDep, namespace: str, body: AddMemberRequest
) -> MemberList:
    """Add or re-role an account in a namespace. Adding a `member` needs manage-members (admin+);
    granting `admin`/`owner` needs manage-roles (owner)."""
    require_capability(repo, account, namespace, Capability.MANAGE_MEMBERS)
    if body.role not in VALID_NS_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid_role")
    if body.role in ("owner", "admin"):
        require_capability(repo, account, namespace, Capability.MANAGE_ROLES)
    target = repo.account_by_name(body.account)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="account_not_found")
    repo.add_member(namespace, int(target["id"]), body.role)
    return MemberList(
        namespace=namespace,
        members=[MemberEntry(account=r["account"], role=r["role"]) for r in repo.list_members(namespace)],
    )


@router.delete("/{namespace}/members/{member}", response_model=MemberList)
def remove_member(
    repo: RepoDep, account: AccountDep, namespace: str, member: str
) -> MemberList:
    """Revoke an account's access to a namespace (removes the membership row — namespace-scoped, not
    a global key revocation). Needs manage-members (admin+); removing an **owner** needs manage-roles
    (owner). Refuses to remove the last owner."""
    require_capability(repo, account, namespace, Capability.MANAGE_MEMBERS)
    target = repo.account_by_name(member)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="account_not_found")
    target_role = repo.namespace_role(namespace, int(target["id"]))
    if target_role == "owner":
        require_capability(repo, account, namespace, Capability.MANAGE_ROLES)  # only owners remove owners
        if repo.count_namespace_owners(namespace) <= 1:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="last_owner")
    if not repo.remove_member(namespace, int(target["id"])):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not_a_member")
    return MemberList(
        namespace=namespace,
        members=[MemberEntry(account=r["account"], role=r["role"]) for r in repo.list_members(namespace)],
    )
