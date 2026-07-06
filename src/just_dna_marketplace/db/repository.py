"""
Data-access layer over the SQLite catalog. Raw SQL lives here; higher layers map rows to API
models. Everything is a projection of `manifest.json`, so ingest rebuilds rows from a manifest.
"""

import sqlite3
from typing import Any, Optional

from just_dna_format.identity import latest as latest_version
from just_dna_format.manifest import ModuleManifest

_SORT_SQL: dict[str, str] = {
    "downloads": "m.downloads DESC, m.name ASC",
    "recent": "m.updated_at DESC, m.name ASC",
    "name": "m.name ASC",
}


class Repository:
    """Thin repository around a SQLite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ── Accounts / namespaces / keys ────────────────────────────────────────

    def create_account(self, name: str) -> int:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO accounts(name) VALUES (?)", (name,)
        )
        self.conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        row = self.conn.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()
        return int(row["id"])

    def create_account_with_install_id(self, name: str, install_id: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO accounts(name, install_id) VALUES (?, ?)", (name, install_id)
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def account_by_install_id(self, install_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, name, install_id FROM accounts WHERE install_id = ?", (install_id,)
        ).fetchone()

    def account_by_name(self, name: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, name, install_id FROM accounts WHERE name = ?", (name,)
        ).fetchone()

    def namespace_owner(self, namespace: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT account_id FROM namespaces WHERE name = ?", (namespace,)
        ).fetchone()

    def count_namespaces_for_account(self, account_id: int) -> int:
        row = self.conn.execute(
            "SELECT count(*) AS n FROM namespaces WHERE account_id = ?", (account_id,)
        ).fetchone()
        return int(row["n"])

    def add_api_key(self, key: str, account_id: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO api_keys(key, account_id) VALUES (?, ?)",
            (key, account_id),
        )
        self.conn.commit()

    def add_namespace(self, name: str, account_id: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO namespaces(name, account_id) VALUES (?, ?)",
            (name, account_id),
        )
        self.conn.commit()

    def account_for_key(self, key: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT a.id, a.name FROM api_keys k JOIN accounts a ON a.id = k.account_id "
            "WHERE k.key = ?",
            (key,),
        ).fetchone()

    def namespaces_for_account(self, account_id: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT name FROM namespaces WHERE account_id = ? ORDER BY name", (account_id,)
        ).fetchall()
        return [r["name"] for r in rows]

    def account_owns_namespace(self, account_id: int, namespace: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM namespaces WHERE account_id = ? AND name = ?",
            (account_id, namespace),
        ).fetchone()
        return row is not None

    # ── Lookups ─────────────────────────────────────────────────────────────

    def get_module_row(self, namespace: str, name: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM modules WHERE namespace = ? AND name = ?", (namespace, name)
        ).fetchone()

    def version_exists(self, namespace: str, name: str, version: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM versions v JOIN modules m ON m.id = v.module_id "
            "WHERE m.namespace = ? AND m.name = ? AND v.version = ?",
            (namespace, name, version),
        ).fetchone()
        return row is not None

    def get_versions(self, module_id: int, *, include_yanked: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM versions WHERE module_id = ?"
        if not include_yanked:
            sql += " AND yanked = 0"
        return self.conn.execute(sql, (module_id,)).fetchall()

    def modules_in_namespace(self, namespace: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, name FROM modules WHERE namespace = ? ORDER BY name", (namespace,)
        ).fetchall()

    def delete_module(self, namespace: str, name: str) -> list[str]:
        """Hard-delete a module and cascade its versions + facet rows. Returns the versions removed
        (for storage cleanup), or [] if the module didn't exist. Ops-only — not the public API."""
        row = self.get_module_row(namespace, name)
        if row is None:
            return []
        versions = [r["version"] for r in self.get_versions(row["id"])]
        self.conn.execute("DELETE FROM modules WHERE id = ?", (row["id"],))
        self.conn.commit()
        return versions

    def delete_namespace_grant(self, namespace: str) -> None:
        """Free a namespace's ownership so a new key can claim it."""
        self.conn.execute("DELETE FROM namespaces WHERE name = ?", (namespace,))
        self.conn.commit()

    def find_versions_by_digest(self, digest: str) -> list[sqlite3.Row]:
        """Every published version whose artifact matches `digest` (the content identity)."""
        return self.conn.execute(
            "SELECT m.namespace, m.name, v.version, v.yanked FROM versions v "
            "JOIN modules m ON m.id = v.module_id WHERE v.digest = ? "
            "ORDER BY m.namespace, m.name, v.version",
            (digest,),
        ).fetchall()

    def get_manifest_json(
        self, namespace: str, name: str, version: str
    ) -> Optional[str]:
        row = self.conn.execute(
            "SELECT v.manifest_json FROM versions v JOIN modules m ON m.id = v.module_id "
            "WHERE m.namespace = ? AND m.name = ? AND v.version = ?",
            (namespace, name, version),
        ).fetchone()
        return row["manifest_json"] if row else None

    # ── Ingest (manifest -> projection) ──────────────────────────────────────

    def upsert_module(self, manifest: ModuleManifest, updated_at: str) -> int:
        """Insert or update the module-level row from a manifest. Returns module id."""
        ident = manifest.identity
        disp = manifest.display
        existing = self.get_module_row(ident.namespace, ident.name)
        if existing is None:
            cur = self.conn.execute(
                "INSERT INTO modules(namespace, name, title, description, icon, color, "
                "genome_build, license, owner, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    ident.namespace, ident.name, disp.title, disp.description, disp.icon,
                    disp.color, manifest.genome_build, manifest.license, manifest.owner,
                    updated_at,
                ),
            )
            self.conn.commit()
            return int(cur.lastrowid)
        self.conn.execute(
            "UPDATE modules SET title=?, description=?, icon=?, color=?, genome_build=?, "
            "license=?, owner=?, updated_at=? WHERE id=?",
            (
                disp.title, disp.description, disp.icon, disp.color, manifest.genome_build,
                manifest.license, manifest.owner, updated_at, existing["id"],
            ),
        )
        self.conn.commit()
        return int(existing["id"])

    def insert_version(
        self, module_id: int, manifest: ModuleManifest, changelog: str, created_at: str
    ) -> int:
        """Insert a version row + facet rows from a manifest. Returns version id."""
        cur = self.conn.execute(
            "INSERT INTO versions(module_id, version, digest, manifest_json, "
            "compile_success, yanked, changelog, created_at) VALUES (?,?,?,?,?,0,?,?)",
            (
                module_id, manifest.identity.version, manifest.artifact.digest,
                manifest.model_dump_json(), int(manifest.compilation.compile_success),
                changelog, created_at,
            ),
        )
        version_id = int(cur.lastrowid)
        self.conn.executemany(
            "INSERT INTO version_genes(version_id, gene) VALUES (?, ?)",
            [(version_id, g) for g in manifest.stats.genes],
        )
        self.conn.executemany(
            "INSERT INTO version_categories(version_id, category) VALUES (?, ?)",
            [(version_id, c) for c in manifest.stats.categories],
        )
        self.conn.commit()
        return version_id

    def recompute_latest(self, module_id: int) -> None:
        """Set `modules.latest_version` to the highest non-yanked SemVer, or NULL if none."""
        rows = self.get_versions(module_id, include_yanked=False)
        value = latest_version([r["version"] for r in rows]) if rows else None
        self.conn.execute(
            "UPDATE modules SET latest_version = ? WHERE id = ?", (value, module_id)
        )
        self.conn.commit()

    def set_yanked(self, namespace: str, name: str, version: str, yanked: bool) -> bool:
        """Set the yanked flag on a version. Returns True if a row was affected."""
        module = self.get_module_row(namespace, name)
        if module is None:
            return False
        cur = self.conn.execute(
            "UPDATE versions SET yanked = ? WHERE module_id = ? AND version = ?",
            (int(yanked), module["id"], version),
        )
        self.conn.commit()
        if cur.rowcount == 0:
            return False
        self.recompute_latest(int(module["id"]))
        return True

    def increment_downloads(self, namespace: str, name: str) -> None:
        self.conn.execute(
            "UPDATE modules SET downloads = downloads + 1 WHERE namespace = ? AND name = ?",
            (namespace, name),
        )
        self.conn.commit()

    # ── Search / list ─────────────────────────────────────────────────────────

    def search_modules(
        self,
        *,
        q: Optional[str] = None,
        category: Optional[str] = None,
        gene: Optional[str] = None,
        genome_build: Optional[str] = None,
        owner: Optional[str] = None,
        license: Optional[str] = None,
        sort: str = "name",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[sqlite3.Row], int]:
        """Return (rows, total). Only modules with a current (non-yanked) latest are listed."""
        where: list[str] = ["m.latest_version IS NOT NULL"]
        params: list[Any] = []
        if q:
            where.append("(m.title LIKE ? OR m.description LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])
        if genome_build:
            where.append("m.genome_build = ?")
            params.append(genome_build)
        if owner:
            where.append("m.owner = ?")
            params.append(owner)
        if license:
            where.append("m.license = ?")
            params.append(license)
        if gene:
            where.append(
                "m.id IN (SELECT v.module_id FROM versions v "
                "JOIN version_genes g ON g.version_id = v.id "
                "WHERE v.yanked = 0 AND g.gene = ?)"
            )
            params.append(gene)
        if category:
            where.append(
                "m.id IN (SELECT v.module_id FROM versions v "
                "JOIN version_categories c ON c.version_id = v.id "
                "WHERE v.yanked = 0 AND c.category = ?)"
            )
            params.append(category)

        clause = " AND ".join(where)
        order = _SORT_SQL.get(sort, _SORT_SQL["name"])
        total = int(
            self.conn.execute(
                f"SELECT COUNT(*) AS n FROM modules m WHERE {clause}", params
            ).fetchone()["n"]
        )
        rows = self.conn.execute(
            f"SELECT * FROM modules m WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return rows, total
