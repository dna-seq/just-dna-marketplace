"""
Storage backend interface. A module version's files live under a version-scoped key
(`{namespace}/{name}/{version}`), matching SPEC §9's `data/{name}/v{N}/` layout. Version-scoping
(rather than content-addressing by `artifact.digest`) keeps per-version logs and the
version-filled `manifest.json` from colliding across versions that share an identical artifact;
the digest still lives in the manifest as the integrity/content identity.

The MVP ships `LocalStorage`; a production `HfStorage` (HuggingFace Hub) backend implements the
same interface and is wired in a later milestone.
"""

from collections.abc import Mapping
from typing import Optional, Protocol, runtime_checkable


def version_key(namespace: str, name: str, version: str) -> str:
    """The storage key for a published version: `{namespace}/{name}/{version}`."""
    return f"{namespace}/{name}/{version}"


@runtime_checkable
class StorageBackend(Protocol):
    """Store for a compiled module version's files, addressed by a version key."""

    def store_module(self, key: str, files: Mapping[str, bytes]) -> None:
        """Persist every file of a module version under `key` (relative names, may be nested)."""
        ...

    def exists(self, key: str, name: str) -> bool:
        """Whether `name` exists under `key`."""
        ...

    def read_file(self, key: str, name: str) -> bytes:
        """Return the raw bytes of `name` under `key`."""
        ...

    def file_url(self, key: str, name: str) -> Optional[str]:
        """
        An external (CDN/presigned) URL for `name`, or `None` when the backend has no external
        URL and the API should serve the bytes itself (the local backend's behavior).
        """
        ...
