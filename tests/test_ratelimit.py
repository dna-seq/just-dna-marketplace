"""Rate limiting — token buckets per caller × category (SPEC §7)."""

from pathlib import Path

from fastapi.testclient import TestClient

from just_dna_marketplace.api.app import create_app
from just_dna_marketplace.config import Settings


def _app(tmp_path: Path, **over):
    return create_app(Settings(db_path=tmp_path / "m.db", local_storage_dir=tmp_path / "a", **over))


def test_search_rate_limit_trips(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, rate_search_per_min=2))  # capacity 2, negligible refill
    assert client.get("/api/v1/modules").status_code == 200
    assert client.get("/api/v1/modules").status_code == 200
    r = client.get("/api/v1/modules")
    assert r.status_code == 429 and r.json()["detail"] == "rate_limited"


def test_rate_limit_can_be_disabled(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, rate_limit_enabled=False, rate_search_per_min=1))
    for _ in range(5):
        assert client.get("/api/v1/modules").status_code == 200
