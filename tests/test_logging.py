"""Debug-logging configuration + request tracing (behind REGISTRY_DEBUG)."""

import logging
from pathlib import Path

from fastapi.testclient import TestClient

from just_dna_registry.api.app import create_app
from just_dna_registry.config import Settings
from just_dna_registry.logging_setup import configure_logging


def test_debug_sets_root_to_debug() -> None:
    configure_logging(Settings(debug=True))
    assert logging.getLogger().level == logging.DEBUG


def test_non_debug_uses_log_level() -> None:
    configure_logging(Settings(debug=False, log_level="WARNING"))
    assert logging.getLogger().level == logging.WARNING


def test_app_boots_and_traces_requests_in_debug(tmp_path: Path, capsys) -> None:
    app = create_app(
        Settings(debug=True, db_path=tmp_path / "m.db", local_storage_dir=tmp_path / "a")
    )
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    out = capsys.readouterr().out
    assert "GET /health -> 200" in out  # request tracing lands on stdout in debug
