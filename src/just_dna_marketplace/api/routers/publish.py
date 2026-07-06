"""
Publish + yank endpoints (SPEC §8.6–§8.7, §8.9).

Auth, namespace ownership, immutability, and version-format guards are implemented and testable
now. The server-side recompile + HF commit that a full publish requires depends on
`just-dna-pipelines` (compile_module) and an Ensembl reference; that step returns 501 until M1/M4
land. Yank is fully functional.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from just_dna_module.identity import is_valid_version
from pydantic import BaseModel

from just_dna_marketplace.api.deps import (
    Account,
    get_repo,
    require_account,
    require_namespace_member,
)
from just_dna_marketplace.db.repository import Repository

router = APIRouter(prefix="/modules", tags=["publish"])

RepoDep = Annotated[Repository, Depends(get_repo)]
AccountDep = Annotated[Account, Depends(require_account)]


class PublishInit(BaseModel):
    version: str
    changelog: str = ""
    publish_mode: str = "recompile"
    files: list[dict] = []


class YankRequest(BaseModel):
    yanked: bool = True


@router.post("/{namespace}/{name}/versions", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def publish_init(
    repo: RepoDep,
    account: AccountDep,
    namespace: str,
    name: str,
    body: PublishInit,
) -> dict:
    """
    Validate a publish request and reserve the version. Returns 501 at the compile step until
    server-side recompile (M4) is wired; the auth/ownership/immutability/version guards run first.
    """
    require_namespace_member(account, namespace)
    if not is_valid_version(body.version):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid_version")
    if repo.version_exists(namespace, name, body.version):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="version_exists")
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        detail="server_side_recompile_not_implemented",
    )


@router.post("/{namespace}/{name}/versions/{version}/yank")
def yank(
    repo: RepoDep,
    account: AccountDep,
    namespace: str,
    name: str,
    version: str,
    body: YankRequest | None = None,
) -> dict:
    """Set (or clear) the yanked flag on a version. Owner-only."""
    require_namespace_member(account, namespace)
    yanked = body.yanked if body is not None else True
    if not repo.set_yanked(namespace, name, version, yanked):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="version_not_found")
    return {"namespace": namespace, "name": name, "version": version, "yanked": yanked}
