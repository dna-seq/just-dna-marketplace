"""Ops-only hard removal: purges DB rows + artifacts and frees the namespace (not yank)."""

from typing import Callable

from just_dna_format.manifest import ModuleManifest

from just_dna_marketplace.db.repository import Repository


def test_remove_module_purges_db_and_storage(
    app, repo: Repository, seed: Callable[..., ModuleManifest]
) -> None:
    seed("just-dna-seq", "coronary", "1.0.0", genes=["LPA"], categories=["cardio"],
         created_at="2025-01-01T00:00:00Z")
    seed("just-dna-seq", "coronary", "2.0.0", genes=["LPA", "APOE"], categories=["cardio"],
         created_at="2025-06-01T00:00:00Z")
    storage = app.state.storage
    assert storage.exists("just-dna-seq/coronary/2.0.0", "weights.parquet")

    versions = repo.delete_module("just-dna-seq", "coronary")
    storage.remove("just-dna-seq/coronary")

    assert set(versions) == {"1.0.0", "2.0.0"}
    assert repo.get_module_row("just-dna-seq", "coronary") is None
    assert not repo.version_exists("just-dna-seq", "coronary", "1.0.0")
    assert not storage.exists("just-dna-seq/coronary/2.0.0", "weights.parquet")
    # Facet rows cascade-deleted with the versions.
    assert repo.conn.execute("SELECT count(*) FROM version_genes").fetchone()[0] == 0
    assert repo.conn.execute("SELECT count(*) FROM version_categories").fetchone()[0] == 0


def test_remove_version_keeps_other_versions(
    app, repo: Repository, seed: Callable[..., ModuleManifest]
) -> None:
    seed("just-dna-seq", "pathogenic", "1.0.0", genes=["BRCA1"], categories=["clinvar"],
         created_at="2025-01-01T00:00:00Z")
    seed("just-dna-seq", "pathogenic", "1.1.0", genes=["BRCA1", "TP53"], categories=["clinvar"],
         created_at="2025-06-01T00:00:00Z")
    storage = app.state.storage

    assert repo.delete_version("just-dna-seq", "pathogenic", "1.1.0") is True
    storage.remove("just-dna-seq/pathogenic/1.1.0")

    assert not repo.version_exists("just-dna-seq", "pathogenic", "1.1.0")
    assert repo.version_exists("just-dna-seq", "pathogenic", "1.0.0")  # sibling kept
    # latest recomputed down to the surviving version.
    assert repo.get_module_row("just-dna-seq", "pathogenic")["latest_version"] == "1.0.0"
    assert not storage.exists("just-dna-seq/pathogenic/1.1.0", "weights.parquet")
    assert storage.exists("just-dna-seq/pathogenic/1.0.0", "weights.parquet")
    # unknown version → False
    assert repo.delete_version("just-dna-seq", "pathogenic", "9.9.9") is False


def test_remove_namespace_frees_it_for_reuse(
    app, repo: Repository, seed: Callable[..., ModuleManifest]
) -> None:
    account_id = repo.create_account("acct")
    repo.add_namespace("just-dna-seq", account_id)
    seed("just-dna-seq", "coronary", "1.0.0", genes=["LPA"], categories=["cardio"],
         created_at="2025-01-01T00:00:00Z")
    seed("just-dna-seq", "lipids", "1.0.0", genes=["PCSK9"], categories=["lipid"],
         created_at="2025-01-01T00:00:00Z")
    assert repo.account_owns_namespace(account_id, "just-dna-seq")

    for module in repo.modules_in_namespace("just-dna-seq"):
        repo.delete_module("just-dna-seq", module["name"])
        app.state.storage.remove(f"just-dna-seq/{module['name']}")
    repo.delete_namespace_grant("just-dna-seq")

    assert repo.modules_in_namespace("just-dna-seq") == []
    assert not repo.account_owns_namespace(account_id, "just-dna-seq")  # freed for a new key
    # A fresh grant + module can reclaim the name (uniqueness freed).
    new_account = repo.create_account("newowner")
    repo.add_namespace("just-dna-seq", new_account)
    assert repo.account_owns_namespace(new_account, "just-dna-seq")
