"""Account profile (0.8.0): email + display_name + a GitHub-style user|org `type` on the single
account primitive. Auth stays token-based; email is private (returned only from whoami)."""

from fastapi.testclient import TestClient

from just_dna_marketplace.db.repository import Repository

_WHOAMI = "/api/v1/auth/whoami"


def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def test_whoami_defaults(client: TestClient, api_key: str) -> None:
    body = client.get(_WHOAMI, headers=_auth(api_key)).json()
    assert body["account"] == "antonkulaga"
    assert body["type"] == "user"  # discriminator defaults to user
    assert body["email"] is None and body["display_name"] is None


def test_patch_sets_email_and_display_name(client: TestClient, api_key: str) -> None:
    resp = client.patch(
        _WHOAMI, json={"email": "anton@uni.io", "display_name": "Anton K"}, headers=_auth(api_key)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == "anton@uni.io" and resp.json()["display_name"] == "Anton K"
    # Persisted across requests.
    assert client.get(_WHOAMI, headers=_auth(api_key)).json()["email"] == "anton@uni.io"


def test_invalid_email_rejected(client: TestClient, api_key: str) -> None:
    assert client.patch(_WHOAMI, json={"email": "nope"}, headers=_auth(api_key)).status_code == 422


def test_userpic_set_and_validated(client: TestClient, api_key: str) -> None:
    ok = client.patch(_WHOAMI, json={"avatar_url": "https://ex.io/me.png"}, headers=_auth(api_key))
    assert ok.status_code == 200 and ok.json()["avatar_url"] == "https://ex.io/me.png"
    # Non-http(s) URL is rejected; "" clears it.
    assert client.patch(_WHOAMI, json={"avatar_url": "javascript:alert(1)"},
                        headers=_auth(api_key)).status_code == 422
    client.patch(_WHOAMI, json={"avatar_url": ""}, headers=_auth(api_key))
    assert client.get(_WHOAMI, headers=_auth(api_key)).json()["avatar_url"] is None


def test_partial_update_leaves_other_field(client: TestClient, api_key: str) -> None:
    client.patch(_WHOAMI, json={"email": "a@b.io", "display_name": "A"}, headers=_auth(api_key))
    client.patch(_WHOAMI, json={"display_name": "Anton Kulaga"}, headers=_auth(api_key))
    body = client.get(_WHOAMI, headers=_auth(api_key)).json()
    assert body["email"] == "a@b.io" and body["display_name"] == "Anton Kulaga"


def test_empty_string_clears_email(client: TestClient, api_key: str) -> None:
    client.patch(_WHOAMI, json={"email": "a@b.io"}, headers=_auth(api_key))
    client.patch(_WHOAMI, json={"email": ""}, headers=_auth(api_key))
    assert client.get(_WHOAMI, headers=_auth(api_key)).json()["email"] is None


def test_duplicate_email_conflicts(client: TestClient, api_key: str, app) -> None:
    repo: Repository = app.state.repo
    other = repo.create_account("bob")
    repo.add_api_key("mk_live_bob", other)
    client.patch(_WHOAMI, json={"email": "shared@x.io"}, headers=_auth(api_key))
    resp = client.patch(_WHOAMI, json={"email": "shared@x.io"}, headers=_auth("mk_live_bob"))
    assert resp.status_code == 409


def test_org_type_discriminator(client: TestClient, app) -> None:
    repo: Repository = app.state.repo
    org_id = repo.create_account("bigorg")
    repo.set_account_type(org_id, "org")
    repo.add_api_key("mk_live_org", org_id)
    assert client.get(_WHOAMI, headers=_auth("mk_live_org")).json()["type"] == "org"


def test_profile_edit_requires_auth(client: TestClient) -> None:
    assert client.patch(_WHOAMI, json={"email": "a@b.io"}).status_code == 401
