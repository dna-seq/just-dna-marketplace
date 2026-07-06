"""
Storage backend interface. Artifacts are content-addressed by their manifest `artifact.digest`,
which gives dedup + immutability for free. The MVP ships a `LocalStorage` backend; the production
`HfStorage` (HuggingFace Hub) backend implements the same interface and is wired in a later
milestone.
"""

from collections.abc import Mapping
from typing import Optional, Protocol, runtime_checkable


def digest_key(digest: str) -> str:
    """Turn a `sha256:<hex>` digest into a filesystem/URL-safe key segment `sha256/<hex>`."""
    return digest.replace("sha256:", "sha256/", 1)


@runtime_checkable
class StorageBackend(Protocol):
    """Content-addressed store for compiled module directories."""

    def store_module(self, digest: str, files: Mapping[str, bytes]) -> None:
        """Persist every file of a module version under its content-addressed digest."""
        ...

    def exists(self, digest: str, name: str) -> bool:
        """Whether `name` exists under `digest`."""
        ...

    def read_file(self, digest: str, name: str) -> bytes:
        """Return the raw bytes of `name` under `digest`."""
        ...

    def file_url(self, digest: str, name: str) -> Optional[str]:
        """
        An external (CDN/presigned) URL for `name`, or `None` when the backend has no external
        URL and the API should serve the bytes itself (the local backend's behavior).
        """
        ...
