"""Optional JWT sessions — backwards-compatible with static API keys."""

from pathlib import Path

from fastapi.testclient import TestClient

from just_dna_registry.api.app import create_app
from just_dna_registry.config import Settings


def _app(tmp_path: Path, **over):
    app = create_app(Settings(db_path=tmp_path / "m.db", local_storage_dir=tmp_path / "a", **over))
    repo = app.state.repo
    acct = repo.create_account("alice")
    repo.add_namespace("alice", acct)
    repo.add_api_key("mk_live_static", acct)
    return app


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def test_jwt_disabled_by_default(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))  # no jwt_secret
    # Static key still works (0.4 behaviour unchanged).
    assert client.get("/api/v1/auth/whoami", headers=_auth("mk_live_static")).json()["account"] == "alice"
    # Token exchange is unavailable.
    assert client.post("/api/v1/auth/tokens", json={"api_key": "mk_live_static"}).status_code == 501


def test_jwt_exchange_and_accept(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, jwt_secret="test-secret-at-least-32-bytes-long!!"))
    # Static key still works alongside JWT.
    assert client.get("/api/v1/auth/whoami", headers=_auth("mk_live_static")).status_code == 200

    resp = client.post("/api/v1/auth/tokens", json={"api_key": "mk_live_static"})
    assert resp.status_code == 200
    jwt_token = resp.json()["token"]
    assert resp.json()["token_type"] == "Bearer" and resp.json()["expires_in"] > 0

    # The JWT is accepted as a bearer.
    who = client.get("/api/v1/auth/whoami", headers=_auth(jwt_token)).json()
    assert who == {
        "account": "alice", "namespaces": ["alice"],
        "type": "user", "display_name": None, "avatar_url": None,
        "funding_url": None, "email": None,
    }

    # Bad key can't mint; garbage bearer is rejected.
    assert client.post("/api/v1/auth/tokens", json={"api_key": "nope"}).status_code == 401
    assert client.get("/api/v1/auth/whoami", headers=_auth("a.b.c")).status_code == 401
