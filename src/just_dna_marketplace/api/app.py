"""
FastAPI application factory.

Builds the app, opens the SQLite catalog, selects a storage backend, and mounts the routers under
`/api/v1`. `create_app(settings)` takes explicit settings so tests can point at a temp DB and a
local artifact dir.
"""

from typing import Optional

from fastapi import FastAPI

from just_dna_marketplace.api.routers import auth, modules, publish
from just_dna_marketplace.config import API_PREFIX, Settings, get_settings
from just_dna_marketplace.db.repository import Repository
from just_dna_marketplace.db.schema import connect, init_db
from just_dna_marketplace.storage.base import StorageBackend
from just_dna_marketplace.storage.local import LocalStorage


def _build_storage(settings: Settings) -> StorageBackend:
    if settings.storage_backend == "local":
        return LocalStorage(settings.local_storage_dir)
    raise ValueError(
        f"unsupported storage_backend {settings.storage_backend!r} "
        "(only 'local' is wired; 'hf' arrives with HfStorage)"
    )


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="just-dna-marketplace", version="0.1.0")

    conn = connect(settings.db_path)
    init_db(conn)
    app.state.settings = settings
    app.state.conn = conn
    app.state.repo = Repository(conn)
    app.state.storage = _build_storage(settings)

    app.include_router(modules.router, prefix=API_PREFIX)
    app.include_router(publish.router, prefix=API_PREFIX)
    app.include_router(auth.router, prefix=API_PREFIX)

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
