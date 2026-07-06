"""
Local filesystem storage backend (dev/test).

Lays out a version's files under `<root>/<key>/<name>` where `key` is `{namespace}/{name}/{version}`
and `name` may be a nested path (e.g. `logs/reviewer.log`). Has no external URL, so the API serves
file bytes itself via the files endpoint.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Optional


class LocalStorage:
    """Version-scoped store rooted at a local directory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _dir(self, key: str) -> Path:
        return self.root / key

    def store_module(self, key: str, files: Mapping[str, bytes]) -> None:
        target = self._dir(key)
        for name, data in files.items():
            dest = target / name
            dest.parent.mkdir(parents=True, exist_ok=True)  # handles nested names (logs/…)
            dest.write_bytes(data)

    def exists(self, key: str, name: str) -> bool:
        return (self._dir(key) / name).is_file()

    def read_file(self, key: str, name: str) -> bytes:
        return (self._dir(key) / name).read_bytes()

    def file_url(self, key: str, name: str) -> Optional[str]:
        # Local backend has no external URL; the API streams the bytes instead.
        return None
