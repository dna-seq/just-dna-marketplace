"""
SQLite catalog schema and connection helper.

The DB is a *projection* of each version's `manifest.json` (the source of truth). Rows exist per
`(namespace, name, version)`, with denormalized module-level fields for the card grid and side
tables (`version_genes`, `version_categories`) for facet filters. SPEC §9.
"""

import sqlite3
from pathlib import Path

SCHEMA: str = """
CREATE TABLE IF NOT EXISTS accounts (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS api_keys (
    key        TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS namespaces (
    name        TEXT PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    featured    INTEGER NOT NULL DEFAULT 0,
    blacklisted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS modules (
    id             INTEGER PRIMARY KEY,
    namespace      TEXT NOT NULL,
    name           TEXT NOT NULL,
    title          TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    icon           TEXT NOT NULL DEFAULT 'database',
    color          TEXT NOT NULL DEFAULT '#6435c9',
    genome_build   TEXT NOT NULL DEFAULT 'GRCh38',
    license        TEXT,
    owner          TEXT,
    readme         TEXT NOT NULL DEFAULT '',
    latest_version TEXT,
    downloads      INTEGER NOT NULL DEFAULT 0,
    stars          INTEGER NOT NULL DEFAULT 0,
    views          INTEGER NOT NULL DEFAULT 0,
    search_hits    INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT '',
    updated_at     TEXT NOT NULL DEFAULT '',
    UNIQUE(namespace, name)
);

CREATE TABLE IF NOT EXISTS versions (
    id              INTEGER PRIMARY KEY,
    module_id       INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    version         TEXT NOT NULL,
    digest          TEXT NOT NULL,
    manifest_json   TEXT NOT NULL,
    compile_success INTEGER NOT NULL DEFAULT 0,
    yanked          INTEGER NOT NULL DEFAULT 0,
    needs_upgrade   INTEGER NOT NULL DEFAULT 0,
    downloads       INTEGER NOT NULL DEFAULT 0,
    changelog       TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT '',
    UNIQUE(module_id, version)
);

CREATE TABLE IF NOT EXISTS version_genes (
    version_id INTEGER NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
    gene       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS version_categories (
    version_id INTEGER NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
    category   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS module_stars (
    module_id  INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    PRIMARY KEY (module_id, account_id)
);

CREATE TABLE IF NOT EXISTS namespace_members (
    namespace  TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    role       TEXT NOT NULL DEFAULT 'contributor',
    PRIMARY KEY (namespace, account_id)
);

-- Reviews/audits (0.8.0): marketplace-layer social data ABOUT a published version — never part of
-- the module manifest (that stays immutable/content-addressed). Anyone authenticated posts one per
-- (version, account); a namespace owner may `highlighted` the good ones (SO accepted-answer style),
-- which is what the `curated` listing group keys on. `verdict` is the optional audit tier.
CREATE TABLE IF NOT EXISTS reviews (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id    INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    version      TEXT NOT NULL,
    account_id   INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    rating       INTEGER NOT NULL,
    verdict      TEXT,
    notes        TEXT,
    highlighted  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL DEFAULT '',
    UNIQUE (module_id, version, account_id)
);

CREATE INDEX IF NOT EXISTS idx_versions_module ON versions(module_id);
CREATE INDEX IF NOT EXISTS idx_version_genes ON version_genes(gene);
CREATE INDEX IF NOT EXISTS idx_version_categories ON version_categories(category);
CREATE INDEX IF NOT EXISTS idx_namespace_members_account ON namespace_members(account_id);
CREATE INDEX IF NOT EXISTS idx_reviews_module ON reviews(module_id);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with row access by name and foreign keys enabled."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")  # wait out brief write contention (threadpool publishes)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables if they do not exist, then run lightweight column migrations."""
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent, additive migrations for existing DBs (the live catalog has data)."""
    acct_cols = {row["name"] for row in conn.execute("PRAGMA table_info(accounts)").fetchall()}
    if "install_id" not in acct_cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN install_id TEXT")
    # Account profile (0.8.0): `email` (private contact/identity, not yet an auth factor),
    # `display_name` (human name, distinct from the `name` handle), and a GitHub-style `type`
    # discriminator (`user`|`org`) so a single identity primitive can be a person or an organization.
    if "email" not in acct_cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN email TEXT")
    if "display_name" not in acct_cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN display_name TEXT")
    if "type" not in acct_cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN type TEXT NOT NULL DEFAULT 'user'")
    # One account per install-id / per email (NULLs are exempt — admin-made or profile-less accounts).
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_install_id "
        "ON accounts(install_id) WHERE install_id IS NOT NULL"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_email "
        "ON accounts(email) WHERE email IS NOT NULL"
    )

    ns_cols = {row["name"] for row in conn.execute("PRAGMA table_info(namespaces)").fetchall()}
    if "featured" not in ns_cols:
        conn.execute("ALTER TABLE namespaces ADD COLUMN featured INTEGER NOT NULL DEFAULT 0")
    if "blacklisted" not in ns_cols:
        conn.execute("ALTER TABLE namespaces ADD COLUMN blacklisted INTEGER NOT NULL DEFAULT 0")

    mod_cols = {row["name"] for row in conn.execute("PRAGMA table_info(modules)").fetchall()}
    # 0.6.0 community/discovery counters (all mirror the existing `downloads` column pattern).
    if "stars" not in mod_cols:
        conn.execute("ALTER TABLE modules ADD COLUMN stars INTEGER NOT NULL DEFAULT 0")
    if "views" not in mod_cols:
        conn.execute("ALTER TABLE modules ADD COLUMN views INTEGER NOT NULL DEFAULT 0")
    if "search_hits" not in mod_cols:
        conn.execute("ALTER TABLE modules ADD COLUMN search_hits INTEGER NOT NULL DEFAULT 0")
    if "created_at" not in mod_cols:
        # First-publish stamp, distinct from `updated_at` (which advances on every republish).
        conn.execute("ALTER TABLE modules ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")

    ver_cols = {row["name"] for row in conn.execute("PRAGMA table_info(versions)").fetchall()}
    if "needs_upgrade" not in ver_cols:
        # Set by the `revalidate` audit when a version no longer satisfies the current contract.
        conn.execute("ALTER TABLE versions ADD COLUMN needs_upgrade INTEGER NOT NULL DEFAULT 0")
    if "downloads" not in ver_cols:  # 0.6.0 per-version download counter
        conn.execute("ALTER TABLE versions ADD COLUMN downloads INTEGER NOT NULL DEFAULT 0")

    # 0.6.0 namespace membership: seed each existing single-owner namespace as an `owner` member,
    # so the new membership check (which supersedes single-owner) sees no disruption. Idempotent.
    conn.execute(
        "INSERT OR IGNORE INTO namespace_members(namespace, account_id, role) "
        "SELECT name, account_id, 'owner' FROM namespaces"
    )
