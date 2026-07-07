"""Auth endpoints: static-key `whoami` (§8.8) + install-id self-registration (community onboarding)."""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from just_dna_format.identity import is_valid_namespace
from pydantic import BaseModel

from just_dna_marketplace.api.deps import (
    Account,
    get_repo,
    require_account,
    settings_dep,
)
from just_dna_marketplace.config import Settings
from just_dna_marketplace.db.repository import Repository
from just_dna_marketplace.installid import validate_install_id
from just_dna_marketplace.jwtauth import issue_jwt, jwt_enabled
from just_dna_marketplace.models.api import WhoAmI

router = APIRouter(prefix="/auth", tags=["auth"])

RepoDep = Annotated[Repository, Depends(get_repo)]
SettingsDep = Annotated[Settings, Depends(settings_dep)]


class RegisterRequest(BaseModel):
    install_id: str
    account: str


class TokenRequest(BaseModel):
    api_key: str


@router.get("/whoami", response_model=WhoAmI)
def whoami(account: Annotated[Account, Depends(require_account)]) -> WhoAmI:
    return WhoAmI(account=account.name, namespaces=account.namespaces)


@router.post("/tokens")
def issue_token(repo: RepoDep, settings: SettingsDep, body: TokenRequest) -> dict:
    """Exchange a static API key for a short-lived JWT session (optional; needs `jwt_secret`)."""
    if not jwt_enabled(settings):
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail="jwt_disabled")
    row = repo.account_for_key(body.api_key)
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid_token")
    token, expires_in = issue_jwt(settings, account_id=int(row["id"]), name=row["name"])
    return {"token": token, "token_type": "Bearer", "expires_in": expires_in}


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(repo: RepoDep, settings: SettingsDep, body: RegisterRequest) -> dict:
    """Self-service onboarding: a valid install-id (proof-of-work) mints an account + API key.

    Community-first — no admin/email. One account per install-id (re-registering an install-id
    just issues a fresh key for its existing account). The account may then claim up to
    `namespaces_per_account` namespaces.
    """
    if not settings.allow_self_register:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="self_register_disabled")
    if not validate_install_id(body.install_id, settings.install_id_difficulty):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid_install_id")

    existing = repo.account_by_install_id(body.install_id)
    if existing is not None:
        account_id, name = int(existing["id"]), existing["name"]
    else:
        if not is_valid_namespace(body.account):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid_account")
        if repo.account_by_name(body.account) is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="account_taken")
        account_id = repo.create_account_with_install_id(body.account, body.install_id)
        name = body.account

    key = "mk_live_" + secrets.token_urlsafe(24)
    repo.add_api_key(key, account_id)
    return {"token": key, "account": name, "namespaces": repo.namespaces_for_account(account_id)}
