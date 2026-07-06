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


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
