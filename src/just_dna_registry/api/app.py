"""
FastAPI application factory.

Builds the app, opens the SQLite catalog, selects a storage backend, and mounts the routers under
`/api/v1`. `create_app(settings)` takes explicit settings so tests can point at a temp DB and a
local artifact dir.
"""

import logging
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, status
from just_dna_format.signing import public_key_b64_from_pem

from just_dna_registry import __version__
from just_dna_registry.api.routers import auth, modules, namespaces, publish, reviews
from just_dna_registry.config import API_PREFIX, Settings, get_settings
from just_dna_registry.version import VersionInfo
from just_dna_registry.db.repository import Repository
from just_dna_registry.db.schema import connect, init_db
from just_dna_registry.logging_setup import configure_logging
from just_dna_registry.ratelimit import default_limiter
from just_dna_registry.startup import validate_hf_access
from just_dna_registry.storage.base import StorageBackend
from just_dna_registry.storage.local import LocalStorage

_request_log = logging.getLogger("registry.request")


def _build_storage(settings: Settings) -> StorageBackend:
    if settings.storage_backend == "local":
        return LocalStorage(settings.local_storage_dir)
    if settings.storage_backend == "hf":
        from just_dna_registry.storage.hf import HfStorage  # imports huggingface_hub lazily

        return HfStorage(settings.hf_repo_id, token=settings.hf_token)
    raise ValueError(f"unsupported storage_backend {settings.storage_backend!r} (use 'local' or 'hf')")


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    validate_hf_access(settings)  # exits(1) if hf backend + missing/read-only token; no-op for local
    app = FastAPI(title="just-dna-registry", version=__version__)

    conn = connect(settings.db_path)
    init_db(conn)
    app.state.settings = settings
    app.state.conn = conn
    app.state.repo = Repository(conn)
    app.state.storage = _build_storage(settings)
    app.state.rate_limiter = default_limiter(settings)
    server_versions = VersionInfo.local()

    @app.middleware("http")
    async def _trace_requests(request: Request, call_next):
        """Trace each request (DEBUG), always log unhandled errors, and stamp the server's versions
        on every response so a client can guard against a contract mismatch without a round-trip."""
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            _request_log.exception(
                "unhandled error: %s %s", request.method, request.url.path
            )
            raise
        response.headers["X-Registry-Version"] = server_versions.registry
        response.headers["X-Format-Version"] = server_versions.format or ""
        response.headers["X-API-Version"] = server_versions.api
        duration_ms = (time.perf_counter() - start) * 1000
        _request_log.debug(
            "%s %s%s -> %s (%.1fms)",
            request.method,
            request.url.path,
            f"?{request.url.query}" if request.url.query else "",
            response.status_code,
            duration_ms,
        )
        return response

    app.include_router(modules.router, prefix=API_PREFIX)
    app.include_router(reviews.router, prefix=API_PREFIX)
    app.include_router(publish.router, prefix=API_PREFIX)
    app.include_router(namespaces.router, prefix=API_PREFIX)
    app.include_router(auth.router, prefix=API_PREFIX)

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        return {"status": "ok", "version": __version__, "storage": settings.storage_backend}

    @app.get(f"{API_PREFIX}/version", tags=["ops"], response_model=VersionInfo)
    def version() -> VersionInfo:
        """The server's API + contract versions, for the client's compatibility guard. A client that
        gets a 404 here is talking to a pre-0.7.1 server (too old to report), and warns accordingly."""
        return server_versions

    @app.get(f"{API_PREFIX}/pubkey", tags=["ops"])
    def pubkey() -> dict:
        """The Ed25519 public key clients pin to verify signed manifests (SPEC §5). 404 when the
        server is not configured to sign."""
        if settings.signing_key is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="signing_not_configured")
        return {
            "algorithm": "ed25519",
            "public_key": public_key_b64_from_pem(settings.signing_key.read_bytes()),
        }

    return app


app = create_app()
