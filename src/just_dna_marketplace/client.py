"""
HTTP client for the marketplace API — powers the `marketplace-client` CLI and live integration
tests. Depends only on `httpx` + the `just-dna-format` contract (for verify-then-install).
"""

import logging
from pathlib import Path
from typing import Any, Optional

import httpx
from just_dna_format.integrity import verify_manifest
from just_dna_format.manifest import ModuleManifest, write_manifest

from just_dna_marketplace.version import VersionInfo, compatibility_error

API_PREFIX: str = "/api/v1"

_log = logging.getLogger("marketplace.client")

# Spec inputs a publisher uploads; compiled outputs are produced server-side, never uploaded.
_SKIP_UPLOAD_SUFFIXES: frozenset[str] = frozenset({".parquet"})
_SKIP_UPLOAD_NAMES: frozenset[str] = frozenset({"manifest.json"})


def gather_spec_files(spec_dir: Path) -> list[tuple[str, bytes]]:
    """Collect uploadable spec files (yaml/csv/md/logo + any logs), as (relative-name, bytes).

    Excludes compiled parquets and manifest.json — the server recompiles. Preserves the `logs/`
    subtree so per-role logs keep their paths.
    """
    spec_dir = Path(spec_dir)
    out: list[tuple[str, bytes]] = []
    for path in sorted(spec_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix in _SKIP_UPLOAD_SUFFIXES or path.name in _SKIP_UPLOAD_NAMES:
            continue
        out.append((path.relative_to(spec_dir).as_posix(), path.read_bytes()))
    return out


class MarketplaceError(RuntimeError):
    """A non-2xx response from the marketplace API."""

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class VersionMismatchError(MarketplaceError):
    """The server and this client disagree on the API / `just-dna-format` contract, so exchanging
    compiled artifacts would collide. Raised before publish/download rather than letting a cryptic
    digest or shape error surface downstream."""

    def __init__(self, message: str, *, server: VersionInfo, client: VersionInfo) -> None:
        # 409 Conflict mirrors the API's "your request conflicts with server state" family.
        super().__init__(409, message)
        self.server = server
        self.client = client


class MarketplaceClient:
    """Thin sync client over the marketplace REST API."""

    def __init__(
        self,
        base_url: str,
        token: Optional[str] = None,
        timeout: float = 600.0,  # publishes recompile server-side; large modules take minutes
        transport: Optional[httpx.BaseTransport] = None,
        check_version: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.local_version = VersionInfo.local()
        headers = {
            # Advertise the client's versions so the server can log/guard the exchange too.
            "X-Marketplace-Client-Version": self.local_version.marketplace,
            "X-Format-Version": self.local_version.format or "",
            "X-API-Version": self.local_version.api,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        # `transport` lets tests drive the ASGI app in-process (httpx.ASGITransport).
        self._http = httpx.Client(
            base_url=self.base_url + API_PREFIX,
            headers=headers,
            timeout=timeout,
            transport=transport,
        )
        self._check_version = check_version
        self._compat_checked = False  # guard runs once per client, lazily

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "MarketplaceClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── Version guard ───────────────────────────────────────────────────────────

    def server_version(self) -> Optional[VersionInfo]:
        """The server's advertised versions, or None if it's too old to report them (pre-0.7.1:
        `GET /version` 404s). Never raises for a plain missing endpoint."""
        resp = self._http.get("/version")
        if resp.status_code == 404:
            return None
        return VersionInfo.model_validate(self._json(resp))

    def assert_compatible(self) -> None:
        """Fail fast if the server and this client are contract-incompatible. Runs once per client;
        a no-op when `check_version=False`. A server too old to report its version can't be checked,
        so it only warns."""
        if not self._check_version or self._compat_checked:
            return
        server = self.server_version()
        if server is None:
            _log.warning(
                "server does not report its version (pre-0.7.1); skipping the compatibility guard"
            )
            self._compat_checked = True
            return
        message = compatibility_error(server, self.local_version)
        if message is not None:
            raise VersionMismatchError(message, server=server, client=self.local_version)
        self._compat_checked = True  # only cache a clean pass, so a mismatch re-raises on retry

    def _json(self, resp: httpx.Response) -> Any:
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise MarketplaceError(resp.status_code, detail)
        return resp.json()

    # ── Reads ─────────────────────────────────────────────────────────────────

    def list_modules(self, **params: Any) -> dict:
        clean = {k: v for k, v in params.items() if v is not None}
        return self._json(self._http.get("/modules", params=clean))

    def get_module(self, namespace: str, name: str) -> dict:
        return self._json(self._http.get(f"/modules/{namespace}/{name}"))

    def versions(self, namespace: str, name: str) -> dict:
        return self._json(self._http.get(f"/modules/{namespace}/{name}/versions"))

    def manifest(self, namespace: str, name: str, version: str) -> ModuleManifest:
        data = self._json(self._http.get(f"/modules/{namespace}/{name}/versions/{version}/manifest"))
        return ModuleManifest.model_validate(data)

    def logs(self, namespace: str, name: str, version: str) -> list[dict]:
        return self._json(
            self._http.get(f"/modules/{namespace}/{name}/versions/{version}/logs")
        )["items"]

    def lookup_by_digest(self, digest: str) -> list[dict]:
        return self._json(self._http.get("/modules/lookup", params={"digest": digest}))["matches"]

    def lookup_by_digests(self, digests: list[str]) -> dict[str, list[dict]]:
        """Batch digest lookup → `{digest: matches}`. Classify many local modules in one request."""
        results = self._json(self._http.post("/modules/lookup", json={"digests": digests}))["results"]
        return {r["digest"]: r["matches"] for r in results}

    # ── Onboarding (community self-service) ──────────────────────────────────

    def register(self, install_id: str, account: str) -> dict:
        """Register an install-id → `{token, account, namespaces}`. No auth (mints the token)."""
        return self._json(
            self._http.post("/auth/register", json={"install_id": install_id, "account": account})
        )

    def namespace_available(self, namespace: str) -> dict:
        return self._json(self._http.get(f"/namespaces/{namespace}"))

    def claim_namespace(self, namespace: str) -> dict:
        """Claim an available namespace for the token's account (bearer)."""
        return self._json(self._http.post("/namespaces", json={"namespace": namespace}))

    def _fetch_file(self, namespace: str, name: str, version: str, rel: str) -> bytes:
        resp = self._http.get(f"/modules/{namespace}/{name}/versions/{version}/files/{rel}")
        if resp.status_code >= 400:
            raise MarketplaceError(resp.status_code, resp.text)
        return resp.content

    def pubkey(self) -> Optional[str]:
        """The server's Ed25519 public key (base64) for pinning, or None if it doesn't sign."""
        resp = self._http.get("/pubkey")
        if resp.status_code == 404:
            return None
        return self._json(resp)["public_key"]

    def download(
        self,
        namespace: str,
        name: str,
        version: str,
        dest: Path,
        *,
        include_logs: bool = True,
        public_key: Optional[str] = None,
    ) -> ModuleManifest:
        """Download a version's artifact (+ logs/logo/provenance) into `dest` and verify it.

        When `public_key` (base64 raw, pinned out-of-band) is given, the manifest's Ed25519
        signature over `artifact.digest` is enforced. Returns the verified manifest."""
        self.assert_compatible()  # a format mismatch shows up as a digest failure — catch it first
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        listing = self._json(
            self._http.get(f"/modules/{namespace}/{name}/versions/{version}/download")
        )
        manifest = self.manifest(namespace, name, version)
        names = [f["name"] for f in listing["files"]]
        if include_logs:
            names += [e["name"] for e in self.logs(namespace, name, version)]
        if manifest.logo is not None:
            names.append(manifest.logo.name)
        if manifest.provenance is not None and manifest.provenance.file:
            names.append(manifest.provenance.file)
        for rel in names:
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(self._fetch_file(namespace, name, version, rel))
        write_manifest(manifest, dest / "manifest.json")
        verify_manifest(
            dest,
            manifest,
            check_logs=include_logs,
            check_logo=manifest.logo is not None,
            check_provenance=manifest.provenance is not None,
            public_key=public_key,
        )
        return manifest

    # ── Publish ────────────────────────────────────────────────────────────────

    def publish(
        self, namespace: str, name: str, version: str, spec_dir: Path, changelog: str = ""
    ) -> ModuleManifest:
        """Upload a spec directory and publish it as a new version (server-side recompile)."""
        self.assert_compatible()
        files = [
            ("files", (rel, data, "application/octet-stream"))
            for rel, data in gather_spec_files(spec_dir)
        ]
        resp = self._http.post(
            f"/modules/{namespace}/{name}/versions",
            data={"version": version, "changelog": changelog},
            files=files,
        )
        return ModuleManifest.model_validate(self._json(resp))

    def import_module(
        self,
        namespace: str,
        name: str,
        version: str,
        archive_path: Path,
        *,
        changelog: str = "",
        display: Optional[dict] = None,
    ) -> ModuleManifest:
        """Publish from a zip/tar.gz archive (spec archive or legacy parquet-only + `display`)."""
        self.assert_compatible()
        archive_path = Path(archive_path)
        data = {"version": version, "changelog": changelog}
        for key in ("title", "description", "report_title", "icon", "color"):
            if display and display.get(key) is not None:
                data[key] = display[key]
        resp = self._http.post(
            f"/modules/{namespace}/{name}/versions/import",
            data=data,
            files={"archive": (archive_path.name, archive_path.read_bytes(), "application/octet-stream")},
        )
        return ModuleManifest.model_validate(self._json(resp))

    def amend_changelog(
        self, namespace: str, name: str, version: str, changelog: str, *, append: bool = False
    ) -> dict:
        """Amend a published version's changelog (metadata only; owner token). Returns the new state."""
        resp = self._http.patch(
            f"/modules/{namespace}/{name}/versions/{version}",
            json={"changelog": changelog, "append": append},
        )
        return self._json(resp)

    def amend_logo(
        self, namespace: str, name: str, version: str, logo_path: Path
    ) -> dict:
        """Replace a published version's logo (owner token; out-of-digest, no version bump)."""
        logo_path = Path(logo_path)
        resp = self._http.post(
            f"/modules/{namespace}/{name}/versions/{version}/logo",
            files={"logo": (logo_path.name, logo_path.read_bytes(), "application/octet-stream")},
        )
        return self._json(resp)

    def get_tarball(self, namespace: str, name: str, version: str, dest: Path) -> Path:
        """Download a version as a single streamable `tar.gz` to `dest`. Returns the path."""
        resp = self._http.get(
            f"/modules/{namespace}/{name}/versions/{version}/download", params={"format": "tarball"}
        )
        if resp.status_code >= 400:
            raise MarketplaceError(resp.status_code, resp.text)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return dest
