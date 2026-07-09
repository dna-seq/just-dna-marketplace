"""
Server-side publish (SPEC §7, §8.6–§8.7): the trust-bearing path.

The publisher uploads the **spec only** (as individual files or a zip/tar.gz archive); the server
validates it, runs `compile_module` itself (so `compile_success`, input hashes, and the artifact
digest are produced by the trusted party), fills the registry-level manifest fields, stores the
compiled module under a version key, and indexes the manifest.

Legacy modules that ship only compiled parquets (the old zip format, no manifest) are imported by
reverse-engineering a spec (`reverse_module`) with the client supplying the missing display
metadata, then recompiling — same trust guarantee.
"""

import io
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Mapping, Optional

from eliot import start_action
from just_dna_compiler.compiler import compile_module, reverse_module, validate_spec
from just_dna_format.identity import canonical_id
from just_dna_format.integrity import sha256_bytes
from just_dna_format.manifest import (
    LOGO_EXTENSIONS,
    MARKETPLACE_COMPILED_BY,
    FileEntry,
    ModuleManifest,
    write_manifest,
)
from just_dna_format.signing import sign_digest

from just_dna_registry.config import Settings
from just_dna_registry.db.repository import Repository
from just_dna_registry.services.ingest import ingest_manifest, now_iso
from just_dna_registry.storage.base import StorageBackend, version_key

REQUIRED_SPEC_FILES: tuple[str, ...] = ("module_spec.yaml", "variants.csv", "studies.csv")
_REVERSE_MARKER: str = "weights.parquet"  # a legacy compiled module has this but no spec


class PublishError(Exception):
    """A publish failure the router maps to an HTTP status."""

    def __init__(
        self, detail: str, *, errors: list[str] | None = None, warnings: list[str] | None = None
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.errors = errors or []
        self.warnings = warnings or []


# ── Public entry points ───────────────────────────────────────────────────────


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
    """Publish from individually-uploaded spec files."""
    missing = [f for f in REQUIRED_SPEC_FILES if f not in files]
    if missing:
        raise PublishError("missing_spec_files", errors=[f"missing: {m}" for m in missing])
    with tempfile.TemporaryDirectory() as tmp:
        spec_dir = Path(tmp) / "spec"
        for rel, data in files.items():
            dest = spec_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        return _finalize(
            repo=repo, storage=storage, settings=settings, namespace=namespace, name=name,
            version=version, changelog=changelog, owner=owner, spec_dir=spec_dir,
        )


def import_archive(
    *,
    repo: Repository,
    storage: StorageBackend,
    settings: Settings,
    namespace: str,
    name: str,
    version: str,
    changelog: str,
    owner: str,
    archive: bytes,
    display: Optional[dict] = None,
) -> ModuleManifest:
    """Publish from a zip/tar.gz archive: a spec archive is compiled directly; a legacy
    parquet-only archive is reverse-engineered (with client-supplied `display` metadata) first."""
    with start_action(
        action_type="import_archive", namespace=namespace, name=name, version=version,
        archive_bytes=len(archive),
    ) as action:
        with tempfile.TemporaryDirectory() as tmp:
            extracted = Path(tmp) / "extracted"
            extracted.mkdir()
            _extract_archive(archive, extracted)
            root = _module_root(extracted)
            is_spec = (root / "module_spec.yaml").is_file()
            action.log(message_type="archive_extracted", mode="spec" if is_spec else "reverse",
                       root=str(root.relative_to(extracted)) or ".")
            if is_spec:
                spec_dir = root
            else:
                spec_dir = Path(tmp) / "reversed"
                reverse_module(root, spec_dir, module_name=name, **(_reverse_kwargs(display)))
            return _finalize(
                repo=repo, storage=storage, settings=settings, namespace=namespace, name=name,
                version=version, changelog=changelog, owner=owner, spec_dir=spec_dir,
            )


# ── Core ──────────────────────────────────────────────────────────────────────


def _finalize(
    *,
    repo: Repository,
    storage: StorageBackend,
    settings: Settings,
    namespace: str,
    name: str,
    version: str,
    changelog: str,
    owner: str,
    spec_dir: Path,
) -> ModuleManifest:
    """Validate + recompile a prepared spec dir, store the version, and index it."""
    with start_action(
        action_type="publish_finalize", namespace=namespace, name=name, version=version
    ) as action:
        validation = validate_spec(spec_dir)
        action.log(
            message_type="validated", valid=validation.valid,
            errors=validation.errors[:20], warnings=validation.warnings[:20],
        )
        if not validation.valid:
            raise PublishError(
                "invalid_spec", errors=validation.errors, warnings=validation.warnings
            )

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            result = compile_module(
                spec_dir,
                out_dir,
                resolve_with_ensembl=settings.resolve_with_ensembl,
                ensembl_cache=settings.ensembl_cache,
                # Trust token stamped into the manifest + enforced by just-dna-format's
                # verify_manifest. Deliberately kept as the legacy value "marketplace-server" across
                # the registry rebrand: it's an internal token (not user-facing), and changing it
                # would invalidate every already-published manifest until re-baked. Retire at the
                # next just-dna-format major cleanup, not here.
                compiled_by=MARKETPLACE_COMPILED_BY,
                ensembl_reference=settings.ensembl_reference,
            )
            action.log(
                message_type="compiled", success=result.success,
                errors=result.errors[:20], warnings=result.warnings[:20],
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

            manifest.identity.namespace = namespace
            manifest.identity.version = version
            manifest.identity.canonical_id = canonical_id(namespace, name, version)
            manifest.owner = owner
            manifest.published_at = now_iso()
            if settings.signing_key is not None:
                # Sign the content identity (SPEC §5); clients pin the pubkey served at /pubkey.
                manifest.signature = sign_digest(
                    manifest.artifact.digest,
                    settings.signing_key.read_bytes(),
                    signed_at=manifest.published_at,
                )
            write_manifest(manifest, out_dir / "manifest.json")

            # Carry spec inputs (yaml/csv/md/logo) into the module dir; skip parquets (the
            # recompiled ones in out_dir are authoritative) and anything compile produced (logs).
            for src in spec_dir.rglob("*"):
                if not src.is_file() or src.suffix == ".parquet":
                    continue
                dest = out_dir / src.relative_to(spec_dir).as_posix()
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, dest)

            stored = {
                p.relative_to(out_dir).as_posix(): p.read_bytes()
                for p in out_dir.rglob("*")
                if p.is_file()
            }
            key = version_key(namespace, name, version)
            storage.store_module(key, stored)
            ingest_manifest(repo, manifest, changelog=changelog)
            action.add_success_fields(
                digest=manifest.artifact.digest,
                storage_key=key,
                n_files=len(stored),
                variant_count=manifest.stats.variant_count,
                logs=[e.name for e in manifest.logs],
            )
            return manifest


def amend_logo(
    *,
    repo: Repository,
    storage: StorageBackend,
    namespace: str,
    name: str,
    version: str,
    filename: str,
    data: bytes,
) -> ModuleManifest:
    """Replace a published version's logo without a version bump.

    The logo is out of `artifact.digest` (SPEC §5 / manifest contract), so swapping it leaves the
    content identity — and any signature over it — intact. Mirrors `amend_changelog`: metadata only,
    owner-gated at the router. Updates the stored `manifest.logo` entry, re-stores the logo bytes
    and the refreshed `manifest.json`, and refreshes the DB projection.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in LOGO_EXTENSIONS:
        raise PublishError(
            "invalid_logo", errors=[f"logo must be one of {sorted(LOGO_EXTENSIONS)}, got {filename!r}"]
        )
    raw = repo.get_manifest_json(namespace, name, version)
    if raw is None:
        raise PublishError("version_not_found")
    manifest = ModuleManifest.model_validate_json(raw)

    logo_name = f"logo.{ext}"
    manifest.logo = FileEntry(name=logo_name, sha256=sha256_bytes(data), size=len(data))

    key = version_key(namespace, name, version)
    storage.store_module(
        key,
        {logo_name: data, "manifest.json": manifest.model_dump_json(indent=2).encode("utf-8") + b"\n"},
    )
    repo.set_version_manifest(namespace, name, version, manifest)
    return manifest


# ── Archive helpers ─────────────────────────────────────────────────────────────


def _extract_archive(data: bytes, dest: Path) -> None:
    """Safely extract a zip or tar.gz archive into `dest` (guards path traversal)."""
    buf = io.BytesIO(data)
    if zipfile.is_zipfile(buf):
        buf.seek(0)
        with zipfile.ZipFile(buf) as zf:
            root = dest.resolve()
            for member in zf.namelist():
                if not (dest / member).resolve().is_relative_to(root):
                    raise PublishError("unsafe_archive", errors=[f"path escapes archive: {member}"])
            zf.extractall(dest)
        return
    buf.seek(0)
    try:
        with tarfile.open(fileobj=buf, mode="r:*") as tf:
            tf.extractall(dest, filter="data")  # 'data' filter blocks traversal/special files
    except tarfile.TarError as exc:
        raise PublishError("bad_archive", errors=[f"not a valid zip or tar.gz: {exc}"])


def _module_root(extracted: Path) -> Path:
    """Locate the directory holding the module (spec or legacy parquets) within an extraction."""
    for marker in ("module_spec.yaml", _REVERSE_MARKER):
        hits = sorted(extracted.rglob(marker))
        if hits:
            return hits[0].parent
    raise PublishError(
        "no_module_content",
        errors=["archive contains neither module_spec.yaml nor weights.parquet"],
    )


def _reverse_kwargs(display: Optional[dict]) -> dict:
    """Filter client-supplied display metadata to reverse_module's accepted, non-null keys."""
    allowed = ("title", "description", "report_title", "icon", "color")
    display = display or {}
    return {k: display[k] for k in allowed if display.get(k) is not None}
