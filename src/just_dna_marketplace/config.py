"""
Service configuration (Pydantic settings).

Loads `.env` before reading the environment (per project rules). All runtime knobs live here;
nothing is hardcoded elsewhere.
"""

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

API_PREFIX: str = "/api/v1"


class Settings(BaseSettings):
    """Marketplace settings, overridable via `MARKETPLACE_*` env vars or `.env`."""

    model_config = SettingsConfigDict(env_prefix="MARKETPLACE_", extra="ignore")

    # Catalog DB (SQLite for MVP; the DB is a projection of manifest.json).
    db_path: Path = Path("data/marketplace.db")

    # Artifact storage. `local` is the dev/test backend; `hf` (HuggingFace Hub) is the
    # production backend and is wired in a later milestone.
    storage_backend: str = "local"
    local_storage_dir: Path = Path("data/artifacts")
    hf_repo_id: str = "just-dna-seq/marketplace"
    # HF write token used by the (pending) HfStorage backend to manage the dataset repo. Reads
    # MARKETPLACE_HF_TOKEN first, then the conventional HF_TOKEN that huggingface_hub itself uses.
    hf_token: str | None = Field(
        default=None, validation_alias=AliasChoices("MARKETPLACE_HF_TOKEN", "HF_TOKEN")
    )

    # Server-side recompile (M4) pins one Ensembl reference across the ecosystem.
    ensembl_reference: str = "just-dna-seq/ensembl_variations"
    # Whether the publish compile resolves rsid<->position via Ensembl. Off by default so a
    # deployment without a reference cache still accepts specs that already carry positions;
    # set on (with a cache via JUST_DNA_PIPELINES_CACHE_DIR / MARKETPLACE_ENSEMBL_CACHE) in prod.
    resolve_with_ensembl: bool = False
    ensembl_cache: Path | None = None

    # Pagination.
    default_per_page: int = 20
    max_per_page: int = 100

    # Community self-service onboarding (0.3). Accounts register with an install-id (proof-of-work,
    # see installid.py) and may claim up to `namespaces_per_account` namespaces. Community-first:
    # self-register is on by default; the install-id PoW deters random spambots, not determined ones.
    allow_self_register: bool = True
    install_id_difficulty: int = 20
    namespaces_per_account: int = 5
    lookup_batch_max: int = 256  # cap on digests per batch /modules/lookup

    # Rate limiting (SPEC §7), per caller (API key or IP) × category. In-memory token buckets.
    rate_limit_enabled: bool = True
    rate_publish_per_hour: float = 10
    rate_download_per_hour: float = 1000
    rate_search_per_min: float = 60

    # Optional JWT sessions (backwards-compatible). Static API keys always work; if `jwt_secret`
    # is set, POST /auth/tokens exchanges a key for a short-lived JWT that's also accepted as a
    # bearer. Unset (default) → JWT disabled, static-keys-only (0.4 behaviour unchanged).
    jwt_secret: str | None = None  # use ≥32 bytes (HS256); unset = JWT off
    jwt_ttl_seconds: int = 86400

    # Observability. `debug` turns on verbose structured logging to stdout (request tracing +
    # Eliot publish/import step logs + third-party DEBUG). Off = `log_level` (default INFO).
    debug: bool = False
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
