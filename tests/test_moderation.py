"""Featured + blacklisted namespaces, and API-key revocation (0.4 moderation/ops)."""

from typing import Callable

from fastapi.testclient import TestClient
from just_dna_format.manifest import ModuleManifest

from just_dna_registry.db.repository import Repository


def _names(body: dict) -> set[str]:
    return {i["name"] for i in body["items"]}


def _two_namespaces(repo: Repository, seed: Callable[..., ModuleManifest]) -> None:
    acct = repo.create_account("acct")
    repo.add_namespace("just-dna-seq", acct)
    repo.add_namespace("other", acct)
    seed("just-dna-seq", "longevity", "1.0.0", genes=["CGAS"], categories=["x"],
         created_at="2025-01-01T00:00:00Z")
    seed("other", "othermod", "1.0.0", genes=["LPA"], categories=["y"],
         created_at="2025-01-01T00:00:00Z")


def test_featured_floats_to_top_and_filters(
    client: TestClient, repo: Repository, seed: Callable[..., ModuleManifest]
) -> None:
    _two_namespaces(repo, seed)
    assert repo.set_namespace_flags("just-dna-seq", featured=True)

    items = client.get("/api/v1/modules").json()["items"]
    assert items[0]["name"] == "longevity" and items[0]["featured"] is True  # featured first
    assert any(i["name"] == "othermod" and i["featured"] is False for i in items)

    only = client.get("/api/v1/modules", params={"featured": "true"}).json()
    assert _names(only) == {"longevity"}


def test_blacklist_hides_from_default_but_reachable_directly(
    client: TestClient, repo: Repository, seed: Callable[..., ModuleManifest]
) -> None:
    _two_namespaces(repo, seed)
    assert repo.set_namespace_flags("other", blacklisted=True)

    # Default listing hides the blacklisted namespace…
    assert _names(client.get("/api/v1/modules").json()) == {"longevity"}
    # …but it's returned on opt-in or a direct namespace filter…
    assert "othermod" in _names(client.get("/api/v1/modules", params={"include_blacklisted": "true"}).json())
    assert _names(client.get("/api/v1/modules", params={"namespace": "other"}).json()) == {"othermod"}
    # …and the detail endpoint still serves it directly.
    assert client.get("/api/v1/modules/other/othermod").status_code == 200


def test_set_flags_unknown_namespace(repo: Repository) -> None:
    assert repo.set_namespace_flags("nope", featured=True) is False


def test_revoke_key(client: TestClient, api_key: str, repo: Repository) -> None:
    assert client.get("/api/v1/auth/whoami", headers={"Authorization": f"Bearer {api_key}"}).status_code == 200
    assert repo.revoke_api_key(api_key) is True
    assert client.get("/api/v1/auth/whoami", headers={"Authorization": f"Bearer {api_key}"}).status_code == 401


def test_revoke_account_keys(repo: Repository, api_key: str) -> None:
    assert repo.revoke_api_keys_for_account("antonkulaga") == 1
    assert repo.revoke_api_keys_for_account("ghost") == 0
