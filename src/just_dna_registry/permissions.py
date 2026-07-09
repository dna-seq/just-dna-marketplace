"""
Capability-based RBAC vocabulary (0.9.0). Pure — no DB, no FastAPI — so it is unit-testable and
importable by both `api/deps.py` (the live resolver) and `cli.py`.

Roles are hierarchical: **owner ⊃ admin ⊃ member** (root/group/user). They are assignable at the org
level (cascading to every namespace the org owns) and per-namespace; the effective role on a
namespace is the *highest* of the two (see `higher_role` + `deps.effective_role`).

  member : publish; amend-OWN; yank/unyank-OWN            (own = versions this account authored)
  admin  : member + amend/yank ANY across the namespace + manage namespaces + manage members + curate
  owner  : admin  + assign roles + manage settings (incl. the funding link)

`*_ANY` vs `*_OWN`: routers demand the ANY capability and pass the resource's author; the resolver
downgrades to the OWN variant when the caller authored the resource (see `deps.require_capability`).
"""

from enum import Enum
from typing import Optional


class Capability(str, Enum):
    PUBLISH = "publish"
    AMEND_OWN = "amend_own"
    AMEND_ANY = "amend_any"
    YANK_OWN = "yank_own"
    YANK_ANY = "yank_any"
    MANAGE_NAMESPACES = "manage_namespaces"
    MANAGE_MEMBERS = "manage_members"
    MANAGE_ROLES = "manage_roles"
    MANAGE_SETTINGS = "manage_settings"
    CURATE = "curate"  # highlight/unhighlight reviews (the `curated` trust signal)


ROLE_RANK: dict[str, int] = {"member": 1, "admin": 2, "owner": 3}
VALID_NS_ROLES: frozenset[str] = frozenset(ROLE_RANK)
VALID_ORG_ROLES: frozenset[str] = frozenset(ROLE_RANK)

_MEMBER: frozenset[Capability] = frozenset(
    {Capability.PUBLISH, Capability.AMEND_OWN, Capability.YANK_OWN}
)
_ADMIN: frozenset[Capability] = _MEMBER | {
    Capability.AMEND_ANY,
    Capability.YANK_ANY,
    Capability.MANAGE_NAMESPACES,
    Capability.MANAGE_MEMBERS,
    Capability.CURATE,
}
_OWNER: frozenset[Capability] = _ADMIN | {Capability.MANAGE_ROLES, Capability.MANAGE_SETTINGS}

ROLE_CAPS: dict[str, frozenset[Capability]] = {
    "member": _MEMBER,
    "admin": _ADMIN,
    "owner": _OWNER,
}

# When an `*_ANY` capability is denied, fall back to the `*_OWN` variant if the caller authored the
# resource. Keyed any → own.
OWN_FALLBACK: dict[Capability, Capability] = {
    Capability.AMEND_ANY: Capability.AMEND_OWN,
    Capability.YANK_ANY: Capability.YANK_OWN,
}


def role_has(role: Optional[str], cap: Capability) -> bool:
    """Whether `role` grants `cap` (None role → nothing)."""
    return role is not None and cap in ROLE_CAPS.get(role, frozenset())


def higher_role(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """The higher-privileged of two roles (None = no role); the org-cascade vs per-namespace union."""
    ranked = [r for r in (a, b) if r in ROLE_RANK]
    return max(ranked, key=ROLE_RANK.__getitem__) if ranked else None
