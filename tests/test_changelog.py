"""Changelog amendment — metadata is mutable; the artifact/digest stay immutable."""

from typing import Callable

from fastapi.testclient import TestClient
from just_dna_format.manifest import ModuleManifest


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _changelog(client: TestClient, name: str, version: str) -> str:
    body = client.get(f"/api/v1/modules/just-dna-seq/{name}/versions").json()
    return next(v["changelog"] for v in body["items"] if v["version"] == version)


def test_amend_replaces_changelog(
    client: TestClient, api_key: str, seed: Callable[..., ModuleManifest]
) -> None:
    m = seed("just-dna-seq", "superhuman", "2.0.0", genes=["ACTN3"], categories=["perf"],
             created_at="2025-01-01T00:00:00Z")
    resp = client.patch(
        "/api/v1/modules/just-dna-seq/superhuman/versions/2.0.0",
        json={"changelog": "v2: 99 protective alleles, Mar-2026 refresh"},
        headers=_auth(api_key),
    )
    assert resp.status_code == 200
    assert _changelog(client, "superhuman", "2.0.0") == "v2: 99 protective alleles, Mar-2026 refresh"
    # Artifact/digest untouched.
    manifest = ModuleManifest.model_validate(
        client.get("/api/v1/modules/just-dna-seq/superhuman/versions/2.0.0/manifest").json()
    )
    assert manifest.artifact.digest == m.artifact.digest


def test_amend_append(client: TestClient, api_key: str, seed: Callable[..., ModuleManifest]) -> None:
    seed("just-dna-seq", "superhuman", "2.0.0", genes=["ACTN3"], categories=["perf"],
         created_at="2025-01-01T00:00:00Z")
    client.patch("/api/v1/modules/just-dna-seq/superhuman/versions/2.0.0",
                 json={"changelog": "first"}, headers=_auth(api_key))
    client.patch("/api/v1/modules/just-dna-seq/superhuman/versions/2.0.0",
                 json={"changelog": "addendum", "append": True}, headers=_auth(api_key))
    assert _changelog(client, "superhuman", "2.0.0") == "first\naddendum"


def test_amend_guards(client: TestClient, api_key: str, seed: Callable[..., ModuleManifest]) -> None:
    seed("just-dna-seq", "superhuman", "2.0.0", genes=["ACTN3"], categories=["perf"],
         created_at="2025-01-01T00:00:00Z")
    base = "/api/v1/modules/just-dna-seq/superhuman/versions"
    # no auth
    assert client.patch(f"{base}/2.0.0", json={"changelog": "x"}).status_code == 401
    # unknown version
    assert client.patch(f"{base}/9.9.9", json={"changelog": "x"}, headers=_auth(api_key)).status_code == 404
    # unowned namespace
    assert client.patch(
        "/api/v1/modules/someone-else/mod/versions/1.0.0", json={"changelog": "x"}, headers=_auth(api_key)
    ).status_code == 403
