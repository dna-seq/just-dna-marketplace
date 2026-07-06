"""Auth endpoints. MVP is static API keys, so only `whoami` is exposed (SPEC §8.8)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from just_dna_marketplace.api.deps import Account, require_account
from just_dna_marketplace.models.api import WhoAmI

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/whoami", response_model=WhoAmI)
def whoami(account: Annotated[Account, Depends(require_account)]) -> WhoAmI:
    return WhoAmI(account=account.name, namespaces=account.namespaces)
