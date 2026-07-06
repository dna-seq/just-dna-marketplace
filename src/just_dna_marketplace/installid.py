"""
Install-id: a lightweight, self-certifying identifier the just-dna-lite app mints once at first
run and ties community onboarding to.

It is a proof-of-work token (Hashcash-style): a valid id is one whose SHA-256 has at least
`difficulty` leading zero **bits**. The client grinds a nonce once (seconds); the server verifies
in O(1). The algorithm is fully open-source — so this is **not** malpractice-resistant — but it
raises the per-id cost enough to deter random/bulk AI-spambot account creation while keeping
onboarding self-service (no admin, no email). Bump `difficulty` to raise the bar.

Shared by the client (generate) and server (validate); pure stdlib, no server deps.
"""

import hashlib
import secrets

PREFIX: str = "jdi1"
DEFAULT_DIFFICULTY: int = 20  # leading zero bits (~1M hashes; ~1s once). Configurable server-side.
_MAX_LEN: int = 128


def _leading_zero_bits(digest: bytes) -> int:
    bits = 0
    for byte in digest:
        if byte == 0:
            bits += 8
            continue
        bits += 8 - byte.bit_length()
        break
    return bits


def _pow_bits(install_id: str) -> int:
    return _leading_zero_bits(hashlib.sha256(install_id.encode("utf-8")).digest())


def generate_install_id(difficulty: int = DEFAULT_DIFFICULTY) -> str:
    """Grind a valid install-id: `jdi1_<random>_<nonce>` whose SHA-256 has ≥ `difficulty` leading
    zero bits. Call once and persist locally."""
    base = secrets.token_hex(8)
    nonce = 0
    while True:
        candidate = f"{PREFIX}_{base}_{nonce}"
        if _pow_bits(candidate) >= difficulty:
            return candidate
        nonce += 1


def validate_install_id(install_id: str, difficulty: int = DEFAULT_DIFFICULTY) -> bool:
    """Whether `install_id` is well-formed and satisfies the proof-of-work at `difficulty`."""
    if not isinstance(install_id, str) or len(install_id) > _MAX_LEN:
        return False
    if not install_id.startswith(PREFIX + "_"):
        return False
    return _pow_bits(install_id) >= difficulty
