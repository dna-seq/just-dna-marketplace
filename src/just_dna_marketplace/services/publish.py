"""
Server-side publish (SPEC §7, §8.6–§8.7): the trust-bearing path.

The publisher uploads the **spec only**; the server validates it, runs `compile_module` itself
(so `compile_success`, input hashes, and the artifact digest are produced by the trusted party),
fills the marketplace-level manifest fields, stores the compiled module under a version key, and
indexes the manifest into the catalog DB.
"""

import tempfile
from pathlib import Path
from typing import Mapping

from just_dna_compiler.compiler import compile_module, validate_spec
from just_dna_format.identity import canonical_id
from just_dna_format.manifest import (
    MARKETPLACE_COMPILED_BY,
    ModuleManifest,
    write_manifest,
)

from just_dna_marketplace.config import Settings
from just_dna_marketplace.db.repository import Repository
from just_dna_marketplace.services.ingest import ingest_manifest, now_iso
from just_dna_marketplace.storage.base import StorageBackend, version_key

# Spec files the publisher may upload (module_spec.yaml + variants.csv + studies.csv are required
# for a valid spec; the rest are optional and carried through).
REQUIRED_SPEC_FILES: tuple[str, ...] = ("module_spec.yaml", "variants.csv", "studies.csv")


class PublishError(Exception):
    """A publish failure the router maps to an HTTP status."""

    def __init__(
        self, detail: str, *, errors: list[str] | None = None, warnings: list[str] | None = None
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.errors = errors or []
        self.warnings = warnings or []


def publish_version(
    *,
    repo: Repository,
    storage: StorageBackend,
    settings: Settings,
    namespace: str,
    name: str,
    version: str,
    changelog: str,
    owner: str,
    files: Mapping[str, bytes],
) -> ModuleManifest:
    """Validate + recompile an uploaded spec, store it, and index it. Returns the manifest.

    Raises `PublishError` for a missing/invalid/uncompilable spec or a name mismatch.
    """
    missing = [f for f in REQUIRED_SPEC_FILES if f not in files]
    if missing:
        raise PublishError("missing_spec_files", errors=[f"missing: {m}" for m in missing])

    with tempfile.TemporaryDirectory() as tmp:
        spec_dir = Path(tmp) / "spec"
        out_dir = Path(tmp) / "out"
        for rel, data in files.items():
            dest = spec_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

        validation = validate_spec(spec_dir)
        if not validation.valid:
            raise PublishError(
                "invalid_spec", errors=validation.errors, warnings=validation.warnings
            )

        result = compile_module(
            spec_dir,
            out_dir,
            resolve_with_ensembl=settings.resolve_with_ensembl,
            ensembl_cache=settings.ensembl_cache,
            compiled_by=MARKETPLACE_COMPILED_BY,
            ensembl_reference=settings.ensembl_reference,
        )
        if not result.success or result.manifest is None:
            raise PublishError(
                "compile_failed", errors=result.errors, warnings=result.warnings
            )

        manifest = result.manifest
        if manifest.identity.name != name:
            raise PublishError(
                "name_mismatch",
                errors=[f"module_spec name {manifest.identity.name!r} != path {name!r}"],
            )

        # Fill the marketplace-level fields the local compiler leaves null (SPEC §4).
        manifest.identity.namespace = namespace
        manifest.identity.version = version
        manifest.identity.canonical_id = canonical_id(namespace, name, version)
        manifest.owner = owner
        manifest.published_at = now_iso()
        write_manifest(manifest, out_dir / "manifest.json")

        # Carry the spec inputs into the module dir so the stored version is self-contained,
        # then store everything (parquets, manifest.json, logs, inputs) under the version key.
        for rel, data in files.items():
            dest = out_dir / rel
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
        stored = {
            p.relative_to(out_dir).as_posix(): p.read_bytes()
            for p in out_dir.rglob("*")
            if p.is_file()
        }
        storage.store_module(version_key(namespace, name, version), stored)

        ingest_manifest(repo, manifest, changelog=changelog)
        return manifest
