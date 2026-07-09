"""HfStorage path/URL construction (offline). Live commit/read is integration-tested separately
with a real token + public dataset repo (needs network/creds)."""

from just_dna_registry.storage.hf import HfStorage


def test_hf_paths_and_resolve_url() -> None:
    s = HfStorage("just-dna-seq/registry", token=None)
    key = "just-dna-seq/coronary/1.0.0"
    assert s._repo_path(key, "weights.parquet") == "data/just-dna-seq/coronary/1.0.0/weights.parquet"
    assert s._fs_path(key, "weights.parquet") == (
        "datasets/just-dna-seq/registry/data/just-dna-seq/coronary/1.0.0/weights.parquet"
    )
    assert s.file_url(key, "logs/reviewer.log") == (
        "https://huggingface.co/datasets/just-dna-seq/registry/resolve/main/"
        "data/just-dna-seq/coronary/1.0.0/logs/reviewer.log"
    )


def test_hf_backend_selected_by_config() -> None:
    # storage_backend=hf builds an HfStorage (no network at construction).
    from just_dna_registry.api.app import _build_storage
    from just_dna_registry.config import Settings

    storage = _build_storage(Settings(storage_backend="hf", hf_repo_id="org/repo"))
    assert isinstance(storage, HfStorage)
    assert storage.file_url("a/b/1.0.0", "weights.parquet").startswith(
        "https://huggingface.co/datasets/org/repo/resolve/main/"
    )
