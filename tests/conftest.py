"""Test fixtures: an isolated app (temp DB + local storage) plus a manifest-seeding helper."""

from pathlib import Path
from typing import Callable

import pytest
from fastapi.testclient import TestClient
from just_dna_format.identity import canonical_id
from just_dna_format.integrity import artifact_digest
from just_dna_format.manifest import (
    Artifact,
    Compilation,
    Display,
    FileEntry,
    Identity,
    ModuleManifest,
    Stats,
)
from just_dna_format.integrity import sha256_bytes

from just_dna_registry.api.app import create_app
from just_dna_registry.config import Settings
from just_dna_registry.db.repository import Repository
from just_dna_registry.services.ingest import ingest_manifest
from just_dna_registry.storage.base import version_key

# One shared artifact payload; content differs per module so digests differ.
_ARTIFACT_FILES = ("weights.parquet", "annotations.parquet", "studies.parquet")


def _make_manifest(
    namespace: str,
    name: str,
    version: str,
    *,
    genes: list[str],
    categories: list[str],
    genome_build: str = "GRCh38",
    owner: str = "antonkulaga",
    license: str = "CC-BY-4.0",
) -> tuple[ModuleManifest, dict[str, bytes]]:
    files = {
        fname: f"{namespace}/{name}@{version}:{fname}".encode() for fname in _ARTIFACT_FILES
    }
    entries = [
        FileEntry(name=n, sha256=sha256_bytes(data), size=len(data))
        for n, data in files.items()
    ]
    manifest = ModuleManifest(
        identity=Identity(
            namespace=namespace,
            name=name,
            version=version,
            canonical_id=canonical_id(namespace, name, version),
        ),
        display=Display(
            title=name.replace("_", " ").title(),
            description=f"{name} module",
            report_title=name.title(),
        ),
        genome_build=genome_build,
        license=license,
        owner=owner,
        stats=Stats(
            variant_count=len(genes) * 2,
            study_count=len(genes),
            gene_count=len(genes),
            genes=genes,
            categories=categories,
        ),
        compilation=Compilation(compile_success=True, compiled_by="marketplace-server"),
        artifact=Artifact(digest=artifact_digest(entries), files=entries),
    )
    return manifest, files


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "registry.db",
        storage_backend="local",
        local_storage_dir=tmp_path / "artifacts",
    )


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def repo(app) -> Repository:
    return app.state.repo


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def seed(app) -> Callable[..., ModuleManifest]:
    """Seed a published version: store artifact bytes + index the manifest. Returns the manifest."""

    def _seed(
        namespace: str,
        name: str,
        version: str,
        *,
        genes: list[str],
        categories: list[str],
        created_at: str,
        **kwargs: object,
    ) -> ModuleManifest:
        manifest, files = _make_manifest(
            namespace, name, version, genes=genes, categories=categories, **kwargs  # type: ignore[arg-type]
        )
        app.state.storage.store_module(version_key(namespace, name, version), files)
        ingest_manifest(app.state.repo, manifest, created_at=created_at)
        return manifest

    return _seed


@pytest.fixture
def api_key(repo: Repository) -> str:
    """An account 'antonkulaga' owning the 'just-dna-seq' namespace, with a usable key."""
    account_id = repo.create_account("antonkulaga")
    repo.add_namespace("just-dna-seq", account_id)
    repo.add_api_key("mk_live_testkey", account_id)
    return "mk_live_testkey"
