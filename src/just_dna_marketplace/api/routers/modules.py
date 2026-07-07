"""
Read/catalog + download endpoints (SPEC §8.1–§8.5). All anonymous.
"""

import io
import tarfile
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from just_dna_format.manifest import ModuleManifest
from pydantic import BaseModel

from just_dna_marketplace.api.deps import (
    Pagination,
    get_repo,
    get_storage,
    pagination,
    rate_limit,
    settings_dep,
)
from just_dna_marketplace.config import Settings
from just_dna_marketplace.db.repository import Repository
from just_dna_marketplace.models.api import ModuleCard, ModuleDetail, Page, VersionSummary
from just_dna_marketplace.services import catalog
from just_dna_marketplace.storage.base import StorageBackend, version_key

router = APIRouter(prefix="/modules", tags=["catalog"])

RepoDep = Annotated[Repository, Depends(get_repo)]
StorageDep = Annotated[StorageBackend, Depends(get_storage)]
SettingsDep = Annotated[Settings, Depends(settings_dep)]
PageDep = Annotated[Pagination, Depends(pagination)]


def _digest_matches(repo: Repository, digest: str) -> list[dict]:
    return [
        {"namespace": r["namespace"], "name": r["name"], "version": r["version"],
         "yanked": bool(r["yanked"])}
        for r in repo.find_versions_by_digest(digest)
    ]


class DigestLookup(BaseModel):
    digests: list[str]


@router.get("", response_model=Page[ModuleCard], dependencies=[Depends(rate_limit("search"))])
def list_modules(
    repo: RepoDep,
    page: PageDep,
    q: Optional[str] = None,
    category: Optional[str] = None,
    gene: Optional[str] = None,
    genome_build: Optional[str] = None,
    owner: Optional[str] = None,
    license: Optional[str] = None,
    namespace: Optional[str] = None,
    featured: Optional[bool] = None,
    include_blacklisted: bool = False,
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
        namespace=namespace,
        featured=featured,
        include_blacklisted=include_blacklisted,
        sort=sort,
    )


@router.get("/lookup")
def lookup_by_digest(repo: RepoDep, digest: str) -> dict:
    """Find published versions whose artifact matches `digest` (the content identity, SPEC §6).

    Lets a publisher check whether an already-compiled module is on the marketplace before
    re-uploading. Returns `{matches: [{namespace, name, version, yanked}]}` (empty if none).
    """
    return {"digest": digest, "matches": _digest_matches(repo, digest)}


@router.post("/lookup")
def lookup_batch(repo: RepoDep, settings: SettingsDep, body: DigestLookup) -> dict:
    """Batch variant of digest lookup — classify many local modules in one request (capped at
    `lookup_batch_max`). `{results: [{digest, matches: [...] }]}`."""
    digests = body.digests[: settings.lookup_batch_max]
    return {"results": [{"digest": d, "matches": _digest_matches(repo, d)} for d in digests]}


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


@router.get("/{namespace}/{name}/versions/{version}/logs")
def list_logs(repo: RepoDep, namespace: str, name: str, version: str) -> dict:
    """List a version's optional provenance/run logs (§ manifest.logs), with fetch URLs."""
    manifest = catalog.get_manifest(repo, namespace, name, version)
    if manifest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="version_not_found")
    base = f"/api/v1/modules/{namespace}/{name}/versions/{version}/files"
    return {
        "items": [
            {"name": e.name, "sha256": e.sha256, "size": e.size, "url": f"{base}/{e.name}"}
            for e in manifest.logs
        ]
    }


@router.get("/{namespace}/{name}/versions/{version}/files/{file_path:path}")
def get_file(
    repo: RepoDep,
    storage: StorageDep,
    namespace: str,
    name: str,
    version: str,
    file_path: str,
) -> Response:
    """Serve (or redirect to) any file recorded in the manifest — artifact parquet, provenance
    log (e.g. `logs/reviewer.log`), or input — validated against the manifest listing."""
    manifest = catalog.get_manifest(repo, namespace, name, version)
    if manifest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="version_not_found")
    allowed = (
        {f.name for f in manifest.artifact.files}
        | {e.name for e in manifest.logs}
        | {e.name for e in manifest.inputs}
    )
    if manifest.logo is not None:
        allowed.add(manifest.logo.name)
    if manifest.provenance is not None and manifest.provenance.file:
        allowed.add(manifest.provenance.file)
    if file_path not in allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="file_not_found")

    key = version_key(namespace, name, version)
    external = storage.file_url(key, file_path)
    if external is not None:
        return RedirectResponse(external, status_code=status.HTTP_302_FOUND)
    data = storage.read_file(key, file_path)
    return Response(content=data, media_type="application/octet-stream")


def _build_tarball(storage: StorageBackend, key: str, manifest: ModuleManifest) -> bytes:
    """Build a streamable tar.gz of the whole module version (manifest + artifact + logs + inputs).

    `manifest.json` comes from the DB manifest (authoritative); every other file is read from
    storage, skipping any optional ones (logs/inputs) not present. Deterministic (no mtimes).
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:

        def _add(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        _add("manifest.json", manifest.model_dump_json(indent=2).encode("utf-8") + b"\n")
        entries = [*manifest.artifact.files, *manifest.logs, *manifest.inputs]
        if manifest.logo is not None:
            entries.append(manifest.logo)
        for entry in entries:
            if storage.exists(key, entry.name):
                _add(entry.name, storage.read_file(key, entry.name))
    return buf.getvalue()


@router.get(
    "/{namespace}/{name}/versions/{version}/download",
    dependencies=[Depends(rate_limit("download"))],
)
def download(
    repo: RepoDep,
    storage: StorageDep,
    namespace: str,
    name: str,
    version: str,
    format: str = Query("files", pattern="^(files|tarball)$"),
) -> Response:
    """
    `format=files` (default): per-file descriptors `{name, url, sha256, size}` for
    verify-then-install. `format=tarball`: a streamable `tar.gz` of the whole module version.
    """
    manifest = catalog.get_manifest(repo, namespace, name, version)
    if manifest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="version_not_found")
    repo.increment_downloads(namespace, name)
    key = version_key(namespace, name, version)

    if format == "tarball":
        data = _build_tarball(storage, key, manifest)
        return Response(
            content=data,
            media_type="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="{name}-{version}.tar.gz"'},
        )

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
    return JSONResponse({"digest": manifest.artifact.digest, "files": files})
