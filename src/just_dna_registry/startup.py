"""
Startup guards. Fail fast (exit 1) on misconfiguration that would otherwise surface only at the
first publish — most importantly, a missing / read-only HuggingFace token when the HF storage
backend is selected.
"""

import logging
import sys

from just_dna_registry.config import Settings

logger = logging.getLogger("registry.startup")


def validate_hf_access(settings: Settings) -> None:
    """When `storage_backend == "hf"`, require a valid, write-capable HF token for the dataset repo.

    Exits the process with code 1 on a missing / invalid / read-only token so the server never
    starts in a state where publishing would later fail. No-op for the local backend.
    """
    if settings.storage_backend != "hf":
        return

    from huggingface_hub import HfApi  # server extra; only imported when HF is selected

    if not settings.hf_token:
        logger.error(
            "storage_backend=hf but no HF token — set HF_TOKEN (or REGISTRY_HF_TOKEN)."
        )
        sys.exit(1)

    api = HfApi(token=settings.hf_token)
    try:
        who = api.whoami()  # validates the token (401 if invalid)
        # create_repo(exist_ok=True) is idempotent and requires write access → verifies it AND
        # ensures the dataset repo exists. Raises (403) if the token can't write.
        api.create_repo(settings.hf_repo_id, repo_type="dataset", exist_ok=True)
    except Exception as exc:  # noqa: BLE001 — any failure here is fatal at startup
        logger.error(
            "HF token invalid or lacks write access to dataset %s: %s",
            settings.hf_repo_id,
            exc,
        )
        sys.exit(1)

    logger.info(
        "HF write access OK for dataset %s (user=%s)",
        settings.hf_repo_id,
        who.get("name") if isinstance(who, dict) else who,
    )
