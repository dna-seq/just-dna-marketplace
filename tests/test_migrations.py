"""Additive schema migrations: `init_db` upgrades a pre-existing DB in place (idempotent)."""

from pathlib import Path

from just_dna_marketplace.db.schema import connect, init_db

# A minimal pre-0.5.0 `versions` table — no `needs_upgrade` column.
_OLD_SCHEMA = """
CREATE TABLE modules (id INTEGER PRIMARY KEY, namespace TEXT, name TEXT);
CREATE TABLE versions (
    id INTEGER PRIMARY KEY,
    module_id INTEGER NOT NULL,
    version TEXT NOT NULL,
    digest TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    compile_success INTEGER NOT NULL DEFAULT 0,
    yanked INTEGER NOT NULL DEFAULT 0,
    changelog TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
"""


def _cols(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_init_db_adds_needs_upgrade_to_old_db(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    conn = connect(db)
    conn.executescript(_OLD_SCHEMA)
    conn.commit()
    assert "needs_upgrade" not in _cols(conn, "versions")

    init_db(conn)  # migrates in place
    assert "needs_upgrade" in _cols(conn, "versions")

    # Idempotent: running again is a no-op, and the audit query now resolves the column.
    init_db(conn)
    assert conn.execute("SELECT needs_upgrade FROM versions").fetchall() == []
