"""Startup guards: HF token validation gates server start when the HF backend is selected."""

import pytest

from just_dna_registry.config import Settings
from just_dna_registry.startup import validate_hf_access


def test_local_backend_skips_hf_check() -> None:
    # Local backend must never touch HF — returns cleanly with no token.
    validate_hf_access(Settings(storage_backend="local", hf_token=None))


def test_hf_backend_without_token_exits() -> None:
    with pytest.raises(SystemExit) as exc:
        validate_hf_access(Settings(storage_backend="hf", hf_token=None))
    assert exc.value.code == 1
