"""
Read/catalog + download endpoints (SPEC §8.1–§8.5). All anonymous.
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse

from just_dna_marketplace.api.deps import Pagination, get_repo, get_storage, pagination
from just_dna_marketplace.db.repository import Repository
from just_dna_marketplace.models.api import ModuleCard, ModuleDetail, Page, VersionSummary
from just_dna_marketplace.services import catalog
from just_dna_marketplace.storage.base import StorageBackend, version_key

router = APIRouter(prefix="/modules", tags=["catalog"])

RepoDep = Annotated[Repository, Depends(get_repo)]
StorageDep = Annotated[StorageBackend, Depends(get_storage)]
PageDep = Annotated[Pagination, Depends(pagination)]


@router.get("", response_model=Page[ModuleCard])
def list_modules(
    repo: RepoDep,
    page: PageDep,
    q: Optional[str] = None,
    category: Optional[str] = None,
    gene: Optional[str] = None,
    genome_build: Optional[str] = None,
    owner: Optional[str] = None,
    license: Optional[str] = None,
    sort: str = Query("name", pattern="^(downloads|recent|name)$"),
) -> Page[ModuleCard]:
    return catalog.list_modules(
        repo,
        page=page.page,
        per_page=page.per_page,
        q=q,
        category=category,
        gene=gene,
        genome_build=genome_build,
        owner=owner,
        license=license,
        sort=sort,
    )


@router.get("/{namespace}/{name}", response_model=ModuleDetail)
def get_module(repo: RepoDep, namespace: str, name: str) -> ModuleDetail:
    detail = catalog.module_detail(repo, namespace, name)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="module_not_found")
    return detail


@router.get("/{namespace}/{name}/versions", response_model=Page[VersionSummary])
def list_versions(
    repo: RepoDep, page: PageDep, namespace: str, name: str
) -> Page[VersionSummary]:
    result = catalog.version_page(
        repo, namespace, name, page=page.page, per_page=page.per_page
    )
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="module_not_found")
    return result


@router.get("/{namespace}/{name}/versions/{version}/manifest")
def get_manifest(repo: RepoDep, namespace: str, name: str, version: str) -> dict:
    manifest = catalog.get_manifest(repo, namespace, name, version)
    if manifest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="version_not_found")
    return manifest.model_dump()


@router.get("/{namespace}/{name}/versions/{version}/files/{file}")
def get_file(
    repo: RepoDep,
    storage: StorageDep,
    namespace: str,
    name: str,
    version: str,
    file: str,
) -> Response:
    """Serve (or redirect to) a single artifact file, verified against the manifest listing."""
    manifest = catalog.get_manifest(repo, namespace, name, version)
    if manifest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="version_not_found")
    if file not in {f.name for f in manifest.artifact.files}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="file_not_found")

    key = version_key(namespace, name, version)
    external = storage.file_url(key, file)
    if external is not None:
        return RedirectResponse(external, status_code=status.HTTP_302_FOUND)
    data = storage.read_file(key, file)
    return Response(content=data, media_type="application/octet-stream")


@router.get("/{namespace}/{name}/versions/{version}/download")
def download(
    repo: RepoDep,
    storage: StorageDep,
    namespace: str,
    name: str,
    version: str,
    format: str = Query("files", pattern="^(files)$"),
) -> dict:
    """
    Return per-file download descriptors `{name, url, sha256, size}` for verify-then-install.
    (Tarball redirect is an HF-backend feature added with `HfStorage`.)
    """
    manifest = catalog.get_manifest(repo, namespace, name, version)
    if manifest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="version_not_found")
    repo.increment_downloads(namespace, name)
    key = version_key(namespace, name, version)
    base = f"/api/v1/modules/{namespace}/{name}/versions/{version}/files"
    files = [
        {
            "name": f.name,
            "url": storage.file_url(key, f.name) or f"{base}/{f.name}",
            "sha256": f.sha256,
            "size": f.size,
        }
        for f in manifest.artifact.files
    ]
    return {"digest": manifest.artifact.digest, "files": files}
