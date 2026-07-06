"""Auth, publish-guard, and yank contract tests (SPEC §8.6–§8.9, §13)."""

from typing import Callable

import pytest
from fastapi.testclient import TestClient
from just_dna_module.manifest import ModuleManifest


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_whoami_requires_token(client: TestClient) -> None:
    assert client.get("/api/v1/auth/whoami").status_code == 401
    assert client.get("/api/v1/auth/whoami", headers=_auth("bogus")).status_code == 401


def test_whoami_ok(client: TestClient, api_key: str) -> None:
    body = client.get("/api/v1/auth/whoami", headers=_auth(api_key)).json()
    assert body == {"account": "antonkulaga", "namespaces": ["just-dna-seq"]}


def test_publish_requires_auth(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/modules/just-dna-seq/foo/versions", json={"version": "1.0.0"}
    )
    assert resp.status_code == 401


def test_publish_rejects_unowned_namespace(client: TestClient, api_key: str) -> None:
    resp = client.post(
        "/api/v1/modules/someone-else/foo/versions",
        json={"version": "1.0.0"},
        headers=_auth(api_key),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "not_namespace_member"


def test_publish_rejects_bad_version(client: TestClient, api_key: str) -> None:
    resp = client.post(
        "/api/v1/modules/just-dna-seq/foo/versions",
        json={"version": "not-semver"},
        headers=_auth(api_key),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "invalid_version"


def test_publish_rejects_existing_version(
    client: TestClient, api_key: str, seed: Callable[..., ModuleManifest]
) -> None:
    seed("just-dna-seq", "coronary", "1.0.0", genes=["LPA"], categories=["cardio"],
         created_at="2025-03-01T00:00:00Z")
    resp = client.post(
        "/api/v1/modules/just-dna-seq/coronary/versions",
        json={"version": "1.0.0"},
        headers=_auth(api_key),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "version_exists"


def test_publish_new_version_reaches_compile_stub(client: TestClient, api_key: str) -> None:
    # All guards pass -> the not-yet-implemented recompile step returns 501.
    resp = client.post(
        "/api/v1/modules/just-dna-seq/newmod/versions",
        json={"version": "1.0.0"},
        headers=_auth(api_key),
    )
    assert resp.status_code == 501


def test_yank_hides_version_from_latest(
    client: TestClient, api_key: str, seed: Callable[..., ModuleManifest]
) -> None:
    seed("just-dna-seq", "longevity_variants_2026", "1.0.0", genes=["CGAS"],
         categories=["x"], created_at="2025-01-01T00:00:00Z")
    seed("just-dna-seq", "longevity_variants_2026", "2.0.0", genes=["CGAS"],
         categories=["x"], created_at="2025-06-01T00:00:00Z")

    base = "/api/v1/modules/just-dna-seq/longevity_variants_2026"
    # Yank the latest -> latest falls back to 1.0.0.
    resp = client.post(f"{base}/versions/2.0.0/yank", headers=_auth(api_key))
    assert resp.status_code == 200 and resp.json()["yanked"] is True
    card = client.get(f"{base}").json()
    assert card["latest_version"] == "1.0.0"

    # Un-yank restores 2.0.0 as latest.
    client.post(f"{base}/versions/2.0.0/yank", json={"yanked": False}, headers=_auth(api_key))
    assert client.get(f"{base}").json()["latest_version"] == "2.0.0"


def test_yank_unknown_version_404(client: TestClient, api_key: str) -> None:
    resp = client.post(
        "/api/v1/modules/just-dna-seq/ghost/versions/1.0.0/yank", headers=_auth(api_key)
    )
    assert resp.status_code == 404
