"""
Publish + yank endpoints (SPEC §8.6–§8.7, §8.9).

Publishing is the trust-bearing path: the client uploads the **spec only** as multipart form-data
(the SPEC-sanctioned MVP alternative to presigned PUT); the server validates + recompiles it,
stores the compiled version, and indexes it. Guards run in order — auth (401), namespace ownership
(403), version format (422), immutability (409) — before any compile work.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from just_dna_format.identity import is_valid_version
from pydantic import BaseModel

from just_dna_marketplace.api.deps import (
    Account,
    get_repo,
    get_storage,
    settings_dep,
    require_account,
    require_namespace_member,
)
from just_dna_marketplace.config import Settings
from just_dna_marketplace.db.repository import Repository
from just_dna_marketplace.services import publish as publish_service
from just_dna_marketplace.storage.base import StorageBackend

router = APIRouter(prefix="/modules", tags=["publish"])

RepoDep = Annotated[Repository, Depends(get_repo)]
StorageDep = Annotated[StorageBackend, Depends(get_storage)]
SettingsDep = Annotated[Settings, Depends(settings_dep)]
AccountDep = Annotated[Account, Depends(require_account)]


class YankRequest(BaseModel):
    yanked: bool = True


@router.post("/{namespace}/{name}/versions", status_code=status.HTTP_201_CREATED)
async def publish(
    repo: RepoDep,
    storage: StorageDep,
    settings: SettingsDep,
    account: AccountDep,
    namespace: str,
    name: str,
    version: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()],
    changelog: Annotated[str, Form()] = "",
) -> dict:
    """Publish a new version: validate + server-side recompile the uploaded spec, then index it."""
    require_namespace_member(account, namespace)
    if not is_valid_version(version):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid_version")
    if repo.version_exists(namespace, name, version):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="version_exists")

    uploads = {f.filename: await f.read() for f in files if f.filename}
    try:
        manifest = publish_service.publish_version(
            repo=repo,
            storage=storage,
            settings=settings,
            namespace=namespace,
            name=name,
            version=version,
            changelog=changelog,
            owner=account.name,
            files=uploads,
        )
    except publish_service.PublishError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": exc.detail, "errors": exc.errors, "warnings": exc.warnings},
        )
    return manifest.model_dump()


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
