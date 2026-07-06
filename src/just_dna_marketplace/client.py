"""
HTTP client for the marketplace API — powers the `marketplace-client` CLI and live integration
tests. Depends only on `httpx` + the `just-dna-format` contract (for verify-then-install).
"""

from pathlib import Path
from typing import Any, Optional

import httpx
from just_dna_format.integrity import verify_manifest
from just_dna_format.manifest import ModuleManifest, write_manifest

API_PREFIX: str = "/api/v1"

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


class MarketplaceClient:
    """Thin sync client over the marketplace REST API."""

    def __init__(
        self,
        base_url: str,
        token: Optional[str] = None,
        timeout: float = 120.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        # `transport` lets tests drive the ASGI app in-process (httpx.ASGITransport).
        self._http = httpx.Client(
            base_url=self.base_url + API_PREFIX,
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "MarketplaceClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

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

    def _fetch_file(self, namespace: str, name: str, version: str, rel: str) -> bytes:
        resp = self._http.get(f"/modules/{namespace}/{name}/versions/{version}/files/{rel}")
        if resp.status_code >= 400:
            raise MarketplaceError(resp.status_code, resp.text)
        return resp.content

    def download(
        self, namespace: str, name: str, version: str, dest: Path, *, include_logs: bool = True
    ) -> ModuleManifest:
        """Download a version's artifact (+ logs) into `dest` and verify it. Returns the manifest."""
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        listing = self._json(
            self._http.get(f"/modules/{namespace}/{name}/versions/{version}/download")
        )
        names = [f["name"] for f in listing["files"]]
        if include_logs:
            names += [e["name"] for e in self.logs(namespace, name, version)]
        for rel in names:
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(self._fetch_file(namespace, name, version, rel))
        manifest = self.manifest(namespace, name, version)
        write_manifest(manifest, dest / "manifest.json")
        verify_manifest(dest, manifest, check_logs=include_logs)
        return manifest

    # ── Publish ────────────────────────────────────────────────────────────────

    def publish(
        self, namespace: str, name: str, version: str, spec_dir: Path, changelog: str = ""
    ) -> ModuleManifest:
        """Upload a spec directory and publish it as a new version (server-side recompile)."""
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
