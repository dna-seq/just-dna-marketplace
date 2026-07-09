"""
Organizations (0.9.0): a first-class `type='org'` account that owns namespaces and has members whose
org role cascades to every namespace the org owns (see `deps.effective_role`).

Mirrors the namespace-members surface. Org-scoped actions gate on `org_members` role directly
(`require_org_capability`) — there is no cascade at the org level itself.
"""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from just_dna_format.identity import is_valid_namespace
from pydantic import BaseModel

from just_dna_registry.api.deps import (
    Account,
    get_repo,
    require_account,
    require_org_capability,
    settings_dep,
)
from just_dna_registry.config import Settings
from just_dna_registry.db.repository import Repository
from just_dna_registry.models.api import (
    AddMemberRequest,
    CreateOrgRequest,
    MemberEntry,
    OrgMemberList,
    OrgSettings,
    RoleUpdate,
)
from just_dna_registry.permissions import VALID_ORG_ROLES, Capability

router = APIRouter(prefix="/orgs", tags=["orgs"])

RepoDep = Annotated[Repository, Depends(get_repo)]
SettingsDep = Annotated[Settings, Depends(settings_dep)]
AccountDep = Annotated[Account, Depends(require_account)]


class ClaimBody(BaseModel):
    namespace: str


def _org_id(repo: Repository, org: str) -> int:
    """Resolve an org handle to its account id, 404 unless it exists and is a `type='org'` account."""
    row = repo.account_by_name(org)
    if row is None or repo.account_type(int(row["id"])) != "org":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="org_not_found")
    return int(row["id"])


def _roster(repo: Repository, org: str, org_id: int) -> OrgMemberList:
    return OrgMemberList(
        org=org,
        members=[MemberEntry(account=r["account"], role=r["role"]) for r in repo.list_org_members(org_id)],
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_org(repo: RepoDep, account: AccountDep, body: CreateOrgRequest) -> dict:
    """Create an org account and seed the caller as its `owner`. The org handle follows the same
    slug rules as a namespace/account and must be free."""
    name = body.name
    if not is_valid_namespace(name):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid_org_name")
    if repo.account_by_name(name) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="name_taken")
    org_id = repo.create_account(name)
    repo.set_account_type(org_id, "org")
    repo.add_org_member(org_id, account.id, "owner")
    return {"org": name, "owner": account.name}


@router.get("/{org}/members", response_model=OrgMemberList)
def list_org_members(repo: RepoDep, account: AccountDep, org: str) -> OrgMemberList:
    """List an org's members. Any org member may read."""
    org_id = _org_id(repo, org)
    require_org_capability(repo, account, org_id, Capability.PUBLISH)  # any org role has PUBLISH
    return _roster(repo, org, org_id)


@router.post("/{org}/members", status_code=status.HTTP_201_CREATED, response_model=OrgMemberList)
def add_org_member(
    repo: RepoDep, account: AccountDep, org: str, body: AddMemberRequest
) -> OrgMemberList:
    """Add or re-role an org member. Adding a `member` needs manage-members (admin+); granting
    `admin`/`owner` needs manage-roles (owner)."""
    org_id = _org_id(repo, org)
    require_org_capability(repo, account, org_id, Capability.MANAGE_MEMBERS)
    if body.role not in VALID_ORG_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid_role")
    if body.role in ("owner", "admin"):
        require_org_capability(repo, account, org_id, Capability.MANAGE_ROLES)
    target = repo.account_by_name(body.account)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="account_not_found")
    repo.add_org_member(org_id, int(target["id"]), body.role)
    return _roster(repo, org, org_id)


@router.put("/{org}/members/{member}/role", response_model=OrgMemberList)
def set_org_role(
    repo: RepoDep, account: AccountDep, org: str, member: str, body: RoleUpdate
) -> OrgMemberList:
    """Change a member's org role. Owner-only (manage-roles). Won't demote the last owner."""
    org_id = _org_id(repo, org)
    require_org_capability(repo, account, org_id, Capability.MANAGE_ROLES)
    if body.role not in VALID_ORG_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid_role")
    target = repo.account_by_name(member)
    if target is None or repo.org_role(org_id, int(target["id"])) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not_a_member")
    if (
        repo.org_role(org_id, int(target["id"])) == "owner"
        and body.role != "owner"
        and repo.count_org_owners(org_id) <= 1
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="last_owner")
    repo.add_org_member(org_id, int(target["id"]), body.role)
    return _roster(repo, org, org_id)


@router.delete("/{org}/members/{member}", response_model=OrgMemberList)
def remove_org_member(repo: RepoDep, account: AccountDep, org: str, member: str) -> OrgMemberList:
    """Remove an org member. Manage-members (admin+); removing an owner needs manage-roles and
    won't remove the last owner."""
    org_id = _org_id(repo, org)
    require_org_capability(repo, account, org_id, Capability.MANAGE_MEMBERS)
    target = repo.account_by_name(member)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="account_not_found")
    if repo.org_role(org_id, int(target["id"])) == "owner":
        require_org_capability(repo, account, org_id, Capability.MANAGE_ROLES)
        if repo.count_org_owners(org_id) <= 1:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="last_owner")
    if not repo.remove_org_member(org_id, int(target["id"])):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not_a_member")
    return _roster(repo, org, org_id)


@router.patch("/{org}/settings")
def update_org_settings(
    repo: RepoDep, account: AccountDep, org: str, body: OrgSettings
) -> dict:
    """Edit the org's profile — funding link, display name, avatar, email. Owner-only
    (manage-settings). `""` clears a field."""
    org_id = _org_id(repo, org)
    require_org_capability(repo, account, org_id, Capability.MANAGE_SETTINGS)
    try:
        repo.set_account_profile(
            org_id, email=body.email, display_name=body.display_name,
            avatar_url=body.avatar_url, funding_url=body.funding_url,
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="email_taken")
    row = repo.get_account(org_id)
    return {
        "org": org,
        "display_name": row["display_name"], "avatar_url": row["avatar_url"],
        "funding_url": row["funding_url"], "email": row["email"],
    }


@router.post("/{org}/namespaces", status_code=status.HTTP_201_CREATED)
def create_org_namespace(
    repo: RepoDep, settings: SettingsDep, account: AccountDep, org: str, body: ClaimBody
) -> dict:
    """Claim a namespace **owned by the org** (access flows via the org-role cascade — no personal
    member row). Needs manage-namespaces (admin+)."""
    org_id = _org_id(repo, org)
    require_org_capability(repo, account, org_id, Capability.MANAGE_NAMESPACES)
    namespace = body.namespace
    if not is_valid_namespace(namespace):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid_namespace")
    if repo.namespace_owner(namespace) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="namespace_taken")
    if repo.count_namespaces_for_account(org_id) >= settings.namespaces_per_account:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="namespace_limit_reached")
    repo.add_namespace(namespace, org_id, seed_owner=False)
    return {"namespace": namespace, "org": org}
