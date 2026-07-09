"""Admin ops (0.9.0): export/import the auth graph (accounts + API keys + namespaces + members),
and reset the catalog while keeping the keys. `reset-db` is gated behind a typed RESET confirmation.
The Ed25519 signing key is a separate PEM file (not in the DB) — untouched by any of this."""

from pathlib import Path

from typer.testing import CliRunner

from just_dna_registry.cli import app
from just_dna_registry.config import get_settings
from just_dna_registry.db.repository import Repository
from just_dna_registry.db.schema import connect, init_db


def _seed(seed) -> None:
    seed("just-dna-seq", "coronary", "1.0.0", genes=["LPA"], categories=["c"],
         created_at="2025-01-01T00:00:00Z")


def test_export_auth_captures_the_graph(repo: Repository, seed, api_key: str) -> None:
    _seed(seed)
    dump = repo.export_auth()
    assert any(a["name"] == "antonkulaga" for a in dump["accounts"])
    assert any(k["key"] == "mk_live_testkey" for k in dump["api_keys"])
    assert any(n["name"] == "just-dna-seq" for n in dump["namespaces"])
    assert any(m["role"] == "owner" for m in dump["members"])


def test_reset_keeps_keys_and_wipes_catalog(repo: Repository, seed, api_key: str) -> None:
    _seed(seed)
    assert repo.search_modules()[1] > 0  # a module is indexed
    repo.reset_catalog(keep_auth=True)
    assert repo.search_modules()[1] == 0                              # catalog gone
    assert repo.account_for_key("mk_live_testkey") is not None        # key kept
    acct = repo.account_for_key("mk_live_testkey")
    assert "just-dna-seq" in repo.namespaces_for_account(int(acct["id"]))  # ownership kept


def test_reset_wipe_keys_removes_accounts(repo: Repository, seed, api_key: str) -> None:
    _seed(seed)
    repo.reset_catalog(keep_auth=False)
    assert repo.account_for_key("mk_live_testkey") is None


def test_export_import_roundtrip_to_fresh_db(
    repo: Repository, seed, api_key: str, tmp_path: Path
) -> None:
    _seed(seed)
    dump = repo.export_auth()
    conn2 = connect(tmp_path / "restored.db")
    init_db(conn2)
    repo2 = Repository(conn2)
    counts = repo2.import_auth(dump)
    assert counts["accounts"] >= 1 and counts["api_keys"] >= 1
    # The key + its namespace ownership are restored (auth only — no modules carried over).
    acct = repo2.account_for_key("mk_live_testkey")
    assert acct is not None
    assert "just-dna-seq" in repo2.namespaces_for_account(int(acct["id"]))
    assert repo2.search_modules()[1] == 0


def test_reset_db_cli_requires_typed_confirmation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REGISTRY_DB_PATH", str(tmp_path / "m.db"))
    monkeypatch.setenv("REGISTRY_STORAGE_BACKEND", "local")
    monkeypatch.setenv("REGISTRY_LOCAL_STORAGE_DIR", str(tmp_path / "art"))
    get_settings.cache_clear()
    runner = CliRunner()

    aborted = runner.invoke(app, ["reset-db"], input="nope\n")
    assert aborted.exit_code != 0  # wrong text → aborts

    ok = runner.invoke(app, ["reset-db"], input="RESET\n")
    assert ok.exit_code == 0 and "catalog reset" in ok.stdout
    get_settings.cache_clear()  # don't leak the temp settings to other tests
