"""
Publish + yank endpoints (SPEC §8.6–§8.7, §8.9).

Publishing is the trust-bearing path: the client uploads the **spec only** as multipart form-data
(the SPEC-sanctioned MVP alternative to presigned PUT); the server validates + recompiles it,
stores the compiled version, and indexes it. Guards run in order — auth (401), namespace ownership
(403), version format (422), immutability (409) — before any compile work.
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from just_dna_format.identity import is_valid_version
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from just_dna_marketplace.api.deps import (
    Account,
    get_repo,
    get_storage,
    rate_limit,
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


class ChangelogPatch(BaseModel):
    changelog: str
    append: bool = False  # append to the existing changelog instead of replacing it


@router.post(
    "/{namespace}/{name}/versions",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("publish"))],
)
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
        # compile_module is CPU-heavy (seconds→minutes for large modules) — run it off the event
        # loop so one publish doesn't freeze the server (and drop the connection).
        manifest = await run_in_threadpool(
            publish_service.publish_version,
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


@router.post(
    "/{namespace}/{name}/versions/import",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("publish"))],
)
async def import_archive(
    repo: RepoDep,
    storage: StorageDep,
    settings: SettingsDep,
    account: AccountDep,
    namespace: str,
    name: str,
    version: Annotated[str, Form()],
    archive: Annotated[UploadFile, File()],
    changelog: Annotated[str, Form()] = "",
    title: Annotated[Optional[str], Form()] = None,
    description: Annotated[Optional[str], Form()] = None,
    report_title: Annotated[Optional[str], Form()] = None,
    icon: Annotated[Optional[str], Form()] = None,
    color: Annotated[Optional[str], Form()] = None,
) -> dict:
    """Publish from a zip/tar.gz archive (in-house packaging / legacy import).

    A spec archive is recompiled directly; a legacy parquet-only archive is reverse-engineered
    with the client-supplied display metadata, then recompiled. Same guards as `publish`.
    """
    require_namespace_member(account, namespace)
    if not is_valid_version(version):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid_version")
    if repo.version_exists(namespace, name, version):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="version_exists")

    data = await archive.read()
    try:
        manifest = await run_in_threadpool(
            publish_service.import_archive,
            repo=repo,
            storage=storage,
            settings=settings,
            namespace=namespace,
            name=name,
            version=version,
            changelog=changelog,
            owner=account.name,
            archive=data,
            display={
                "title": title,
                "description": description,
                "report_title": report_title,
                "icon": icon,
                "color": color,
            },
        )
    except publish_service.PublishError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": exc.detail, "errors": exc.errors, "warnings": exc.warnings},
        )
    return manifest.model_dump()


@router.patch("/{namespace}/{name}/versions/{version}")
def amend_changelog(
    repo: RepoDep,
    account: AccountDep,
    namespace: str,
    name: str,
    version: str,
    body: ChangelogPatch,
) -> dict:
    """Amend a published version's changelog. Metadata only — the artifact/digest are immutable
    and untouched. Owner-only. `append=true` adds to the existing changelog."""
    require_namespace_member(account, namespace)
    current = repo.get_version_changelog(namespace, name, version)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="version_not_found")
    changelog = f"{current}\n{body.changelog}" if (body.append and current) else body.changelog
    repo.set_version_changelog(namespace, name, version, changelog)
    return {"namespace": namespace, "name": name, "version": version, "changelog": changelog}


@router.post("/{namespace}/{name}/versions/{version}/logo")
async def amend_logo(
    repo: RepoDep,
    storage: StorageDep,
    account: AccountDep,
    namespace: str,
    name: str,
    version: str,
    logo: Annotated[UploadFile, File()],
) -> dict:
    """Replace a published version's logo (png/jpg/jpeg). Owner-only. Out-of-digest metadata: the
    artifact/digest — and any signature over it — stay immutable, so no version bump is needed."""
    require_namespace_member(account, namespace)
    data = await logo.read()
    try:
        manifest = await run_in_threadpool(
            publish_service.amend_logo,
            repo=repo,
            storage=storage,
            namespace=namespace,
            name=name,
            version=version,
            filename=logo.filename or "",
            data=data,
        )
    except publish_service.PublishError as exc:
        code = (
            status.HTTP_404_NOT_FOUND
            if exc.detail == "version_not_found"
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(code, detail={"error": exc.detail, "errors": exc.errors})
    return {
        "namespace": namespace, "name": name, "version": version,
        "logo": manifest.logo.model_dump() if manifest.logo else None,
    }


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
