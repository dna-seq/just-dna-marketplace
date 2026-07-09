"""
HuggingFace Hub dataset storage backend (SPEC §9 hybrid).

A module version's files live under `data/{namespace}/{name}/{version}/…` in the configured HF
dataset repo. Writes are a single commit; reads go through `HfFileSystem` (fsspec — never
`snapshot_download`, per project rules); `file_url` returns the HF `resolve` CDN URL so the API
`302`-redirects downloads to the CDN instead of proxying bytes.

Requires a write-capable token (validated at startup by `startup.validate_hf_access`) and a
**public** dataset repo so `resolve` URLs are fetchable without auth. Server-tier only.
"""

from collections.abc import Mapping
from typing import Optional

from huggingface_hub import HfApi, HfFileSystem
from huggingface_hub.hf_api import CommitOperationAdd


class HfStorage:
    """Version-scoped store over an HF dataset repo."""

    def __init__(
        self, repo_id: str, token: Optional[str] = None, *, prefix: str = "data", revision: str = "main"
    ) -> None:
        self.repo_id = repo_id
        self.prefix = prefix
        self.revision = revision
        self._api = HfApi(token=token)
        self._fs = HfFileSystem(token=token)

    def _repo_path(self, key: str, name: str) -> str:
        return f"{self.prefix}/{key}/{name}"

    def _fs_path(self, key: str, name: str) -> str:
        return f"datasets/{self.repo_id}/{self._repo_path(key, name)}"

    def store_module(self, key: str, files: Mapping[str, bytes]) -> None:
        operations = [
            CommitOperationAdd(path_in_repo=self._repo_path(key, name), path_or_fileobj=data)
            for name, data in files.items()
        ]
        self._api.create_commit(
            repo_id=self.repo_id,
            repo_type="dataset",
            operations=operations,
            commit_message=f"publish {key}",
            revision=self.revision,
        )

    def exists(self, key: str, name: str) -> bool:
        return self._fs.exists(self._fs_path(key, name))

    def read_file(self, key: str, name: str) -> bytes:
        return self._fs.cat_file(self._fs_path(key, name))

    def file_url(self, key: str, name: str) -> Optional[str]:
        return (
            f"https://huggingface.co/datasets/{self.repo_id}/resolve/"
            f"{self.revision}/{self._repo_path(key, name)}"
        )

    def remove(self, key: str) -> None:
        try:
            self._api.delete_folder(
                path_in_repo=f"{self.prefix}/{key}",
                repo_id=self.repo_id,
                repo_type="dataset",
                revision=self.revision,
            )
        except Exception:  # noqa: BLE001 — idempotent: already-absent is fine
            pass
