"""
FastAPI application factory.

Builds the app, opens the SQLite catalog, selects a storage backend, and mounts the routers under
`/api/v1`. `create_app(settings)` takes explicit settings so tests can point at a temp DB and a
local artifact dir.
"""

import logging
import time
from typing import Optional

from fastapi import FastAPI, Request

from just_dna_marketplace.api.routers import auth, modules, namespaces, publish
from just_dna_marketplace.config import API_PREFIX, Settings, get_settings
from just_dna_marketplace.db.repository import Repository
from just_dna_marketplace.db.schema import connect, init_db
from just_dna_marketplace.logging_setup import configure_logging
from just_dna_marketplace.ratelimit import default_limiter
from just_dna_marketplace.startup import validate_hf_access
from just_dna_marketplace.storage.base import StorageBackend
from just_dna_marketplace.storage.local import LocalStorage

_request_log = logging.getLogger("marketplace.request")


def _build_storage(settings: Settings) -> StorageBackend:
    if settings.storage_backend == "local":
        return LocalStorage(settings.local_storage_dir)
    if settings.storage_backend == "hf":
        from just_dna_marketplace.storage.hf import HfStorage  # imports huggingface_hub lazily

        return HfStorage(settings.hf_repo_id, token=settings.hf_token)
    raise ValueError(f"unsupported storage_backend {settings.storage_backend!r} (use 'local' or 'hf')")


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    validate_hf_access(settings)  # exits(1) if hf backend + missing/read-only token; no-op for local
    app = FastAPI(title="just-dna-marketplace", version="0.4.3")

    conn = connect(settings.db_path)
    init_db(conn)
    app.state.settings = settings
    app.state.conn = conn
    app.state.repo = Repository(conn)
    app.state.storage = _build_storage(settings)
    app.state.rate_limiter = default_limiter(settings)

    @app.middleware("http")
    async def _trace_requests(request: Request, call_next):
        """Trace each request (DEBUG) and always log unhandled errors with a traceback."""
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            _request_log.exception(
                "unhandled error: %s %s", request.method, request.url.path
            )
            raise
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
    app.include_router(publish.router, prefix=API_PREFIX)
    app.include_router(namespaces.router, prefix=API_PREFIX)
    app.include_router(auth.router, prefix=API_PREFIX)

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
