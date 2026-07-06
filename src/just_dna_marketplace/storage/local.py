"""
Local filesystem storage backend (dev/test).

Lays out files content-addressed under `<root>/sha256/<hex>/<name>`. Has no external URL, so the
API serves file bytes itself via the files endpoint.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Optional

from just_dna_marketplace.storage.base import digest_key


class LocalStorage:
    """Content-addressed store rooted at a local directory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _dir(self, digest: str) -> Path:
        return self.root / digest_key(digest)

    def store_module(self, digest: str, files: Mapping[str, bytes]) -> None:
        target = self._dir(digest)
        target.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (target / name).write_bytes(data)

    def exists(self, digest: str, name: str) -> bool:
        return (self._dir(digest) / name).is_file()

    def read_file(self, digest: str, name: str) -> bytes:
        return (self._dir(digest) / name).read_bytes()

    def file_url(self, digest: str, name: str) -> Optional[str]:
        # Local backend has no external URL; the API streams the bytes instead.
        return None
