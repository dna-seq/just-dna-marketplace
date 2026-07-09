"""Install-id proof-of-work, self-registration, namespace claim, and batch digest lookup (0.3)."""

from typing import Callable

import pytest
from fastapi.testclient import TestClient
from just_dna_format.manifest import ModuleManifest

from just_dna_registry.config import Settings
from just_dna_registry.installid import (
    generate_install_id,
    validate_install_id,
)

_DIFF = 8  # low PoW for fast tests


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


# ── install-id algorithm ──────────────────────────────────────────────────────


def test_generated_install_id_validates() -> None:
    iid = generate_install_id(_DIFF)
    assert iid.startswith("jdi1_")
    assert validate_install_id(iid, _DIFF)


def test_invalid_install_ids_rejected() -> None:
    assert not validate_install_id("random-string", _DIFF)
    assert not validate_install_id("jdi1_deadbeef_0", 40)  # PoW almost certainly unmet at 40 bits
    assert not validate_install_id("", _DIFF)


# ── registration + namespace claim (needs low difficulty; use a dedicated app) ─


@pytest.fixture
def onboarding_app(tmp_path):
    from just_dna_registry.api.app import create_app

    return create_app(
        Settings(
            db_path=tmp_path / "m.db",
            local_storage_dir=tmp_path / "a",
            install_id_difficulty=_DIFF,
            namespaces_per_account=2,
        )
    )


@pytest.fixture
def onboarding_client(onboarding_app) -> TestClient:
    return TestClient(onboarding_app)


def test_register_then_claim_namespaces(onboarding_client: TestClient) -> None:
    iid = generate_install_id(_DIFF)
    reg = onboarding_client.post(
        "/api/v1/auth/register", json={"install_id": iid, "account": "alice"}
    )
    assert reg.status_code == 201, reg.text
    token = reg.json()["token"]
    assert reg.json()["account"] == "alice"

    # whoami reflects the new account.
    who = onboarding_client.get("/api/v1/auth/whoami", headers=_auth(token)).json()
    assert who["account"] == "alice"

    # availability + claim.
    assert onboarding_client.get("/api/v1/namespaces/alice-mods").json()["available"] is True
    c1 = onboarding_client.post("/api/v1/namespaces", json={"namespace": "alice-mods"}, headers=_auth(token))
    assert c1.status_code == 201 and c1.json()["owner"] == "alice"
    assert onboarding_client.get("/api/v1/namespaces/alice-mods").json()["available"] is False

    # limit is 2 → second ok, third rejected.
    assert onboarding_client.post("/api/v1/namespaces", json={"namespace": "alice-two"}, headers=_auth(token)).status_code == 201
    over = onboarding_client.post("/api/v1/namespaces", json={"namespace": "alice-three"}, headers=_auth(token))
    assert over.status_code == 403 and over.json()["detail"] == "namespace_limit_reached"


def test_register_rejects_bad_install_id(onboarding_client: TestClient) -> None:
    resp = onboarding_client.post(
        "/api/v1/auth/register", json={"install_id": "not-a-pow", "account": "bob"}
    )
    assert resp.status_code == 422 and resp.json()["detail"] == "invalid_install_id"


def test_claim_taken_namespace_conflicts(onboarding_client: TestClient) -> None:
    def reg(handle):
        iid = generate_install_id(_DIFF)
        return onboarding_client.post(
            "/api/v1/auth/register", json={"install_id": iid, "account": handle}
        ).json()["token"]

    t1, t2 = reg("carol"), reg("dave")
    assert onboarding_client.post("/api/v1/namespaces", json={"namespace": "shared"}, headers=_auth(t1)).status_code == 201
    clash = onboarding_client.post("/api/v1/namespaces", json={"namespace": "shared"}, headers=_auth(t2))
    assert clash.status_code == 409 and clash.json()["detail"] == "namespace_taken"


# ── batch digest lookup ───────────────────────────────────────────────────────


def test_batch_lookup(client: TestClient, seed: Callable[..., ModuleManifest]) -> None:
    m = seed("just-dna-seq", "coronary", "1.0.0", genes=["LPA"], categories=["cardio"],
             created_at="2025-01-01T00:00:00Z")
    body = client.post(
        "/api/v1/modules/lookup", json={"digests": [m.artifact.digest, "sha256:missing"]}
    ).json()
    by_digest = {r["digest"]: r["matches"] for r in body["results"]}
    assert by_digest[m.artifact.digest][0]["name"] == "coronary"
    assert by_digest["sha256:missing"] == []
