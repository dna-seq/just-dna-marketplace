"""
Optional JWT sessions. Backwards-compatible: static API keys always work; JWT is only active when
`settings.jwt_secret` is set. A JWT encodes the account (`sub`, `account_id`) and is accepted as a
bearer alongside static keys. HS256; short-lived.
"""

import datetime
from typing import Optional

import jwt

from just_dna_registry.config import Settings

_ALGO = "HS256"


def jwt_enabled(settings: Settings) -> bool:
    return bool(settings.jwt_secret)


def issue_jwt(settings: Settings, *, account_id: int, name: str) -> tuple[str, int]:
    """Mint a JWT for an account. Returns (token, expires_in_seconds)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    ttl = settings.jwt_ttl_seconds
    payload = {
        "sub": name,
        "account_id": account_id,
        "iat": int(now.timestamp()),
        "exp": int((now + datetime.timedelta(seconds=ttl)).timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=_ALGO)
    return token, ttl


def decode_jwt(settings: Settings, token: str) -> Optional[dict]:
    """Return the claims of a valid, unexpired JWT, or None (disabled / malformed / expired)."""
    if not jwt_enabled(settings):
        return None
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[_ALGO])
    except jwt.PyJWTError:
        return None
