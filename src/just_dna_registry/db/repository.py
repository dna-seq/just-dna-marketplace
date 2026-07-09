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
    "stars": "m.stars DESC, m.name ASC",
    "popular": "(m.views + m.search_hits) DESC, m.name ASC",
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

    def namespace_flags(self, namespace: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT featured, blacklisted FROM namespaces WHERE name = ?", (namespace,)
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
        """Claim a namespace for an account (the founding owner) and seed its owner membership."""
        self.conn.execute(
            "INSERT OR REPLACE INTO namespaces(name, account_id) VALUES (?, ?)",
            (name, account_id),
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO namespace_members(namespace, account_id, role) "
            "VALUES (?, ?, 'owner')",
            (name, account_id),
        )
        self.conn.commit()

    def account_for_key(self, key: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT a.id, a.name FROM api_keys k JOIN accounts a ON a.id = k.account_id "
            "WHERE k.key = ?",
            (key,),
        ).fetchone()

    def get_account(self, account_id: int) -> Optional[sqlite3.Row]:
        """Full account row incl. the 0.8.x profile fields (email/display_name/avatar_url/type)."""
        return self.conn.execute(
            "SELECT id, name, email, display_name, avatar_url, type FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()

    def set_account_profile(
        self,
        account_id: int,
        *,
        email: Optional[str] = None,
        display_name: Optional[str] = None,
        avatar_url: Optional[str] = None,
    ) -> None:
        """Self-service profile update. Only the fields passed are changed; an empty string clears a
        field to NULL (so a user can remove an email/name/userpic)."""
        sets: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("email", email),
            ("display_name", display_name),
            ("avatar_url", avatar_url),
        ):
            if value is not None:
                sets.append(f"{column} = ?")
                params.append(value or None)  # "" clears to NULL
        if not sets:
            return
        params.append(account_id)
        self.conn.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id = ?", params)
        self.conn.commit()

    def set_account_type(self, account_id: int, account_type: str) -> None:
        """Set the account's `user`/`org` discriminator (admin/creation-time, not self-service)."""
        self.conn.execute(
            "UPDATE accounts SET type = ? WHERE id = ?", (account_type, account_id)
        )
        self.conn.commit()

    def namespaces_for_account(self, account_id: int) -> list[str]:
        """Every namespace the account belongs to (owner OR contributor) — feeds `Account.namespaces`
        and thus the publish membership check."""
        rows = self.conn.execute(
            "SELECT namespace FROM namespace_members WHERE account_id = ? ORDER BY namespace",
            (account_id,),
        ).fetchall()
        return [r["namespace"] for r in rows]

    def account_owns_namespace(self, account_id: int, namespace: str) -> bool:
        """Whether the account is an owner of the namespace (membership-aware)."""
        return self.namespace_role(namespace, account_id) == "owner"

    # ── Namespace membership (0.6.0) ─────────────────────────────────────────

    def namespace_role(self, namespace: str, account_id: int) -> Optional[str]:
        """The account's role in the namespace (`owner`/`contributor`), or None if not a member."""
        row = self.conn.execute(
            "SELECT role FROM namespace_members WHERE namespace = ? AND account_id = ?",
            (namespace, account_id),
        ).fetchone()
        return row["role"] if row else None

    def add_member(self, namespace: str, account_id: int, role: str) -> None:
        """Add or promote an account in a namespace (upsert the role)."""
        self.conn.execute(
            "INSERT INTO namespace_members(namespace, account_id, role) VALUES (?, ?, ?) "
            "ON CONFLICT(namespace, account_id) DO UPDATE SET role = excluded.role",
            (namespace, account_id, role),
        )
        self.conn.commit()

    def remove_member(self, namespace: str, account_id: int) -> bool:
        """Revoke an account's membership in a namespace. Returns True if a row was removed."""
        cur = self.conn.execute(
            "DELETE FROM namespace_members WHERE namespace = ? AND account_id = ?",
            (namespace, account_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def list_members(self, namespace: str) -> list[sqlite3.Row]:
        """All members of a namespace as `(account, role)`, owners first then by name."""
        return self.conn.execute(
            "SELECT a.name AS account, m.role AS role FROM namespace_members m "
            "JOIN accounts a ON a.id = m.account_id WHERE m.namespace = ? "
            "ORDER BY (m.role = 'owner') DESC, a.name",
            (namespace,),
        ).fetchall()

    def count_namespace_owners(self, namespace: str) -> int:
        """How many owners a namespace has (guards against removing the last one)."""
        row = self.conn.execute(
            "SELECT count(*) AS n FROM namespace_members WHERE namespace = ? AND role = 'owner'",
            (namespace,),
        ).fetchone()
        return int(row["n"])

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

    # ── Ops: auth export/import + catalog reset ─────────────────────────────────

    def export_auth(self) -> dict[str, list[dict]]:
        """Serialize the auth graph — accounts (+ profile), API keys, namespaces, memberships — for
        backup or preprod→prod migration. Account ids are preserved so the FKs line up on import.
        NB: `api_keys.key` values are the live plaintext tokens — treat the export as a secret."""
        def rows(sql: str) -> list[dict]:
            return [dict(r) for r in self.conn.execute(sql).fetchall()]

        return {
            "accounts": rows(
                "SELECT id, name, email, display_name, avatar_url, type, install_id FROM accounts"
            ),
            "api_keys": rows("SELECT key, account_id FROM api_keys"),
            "namespaces": rows("SELECT name, account_id, featured, blacklisted FROM namespaces"),
            "members": rows("SELECT namespace, account_id, role FROM namespace_members"),
        }

    def import_auth(self, data: dict[str, list[dict]]) -> dict[str, int]:
        """Restore an `export_auth()` payload (idempotent upsert; preserves account ids). Returns
        per-table counts. Existing rows with the same id/key are overwritten (accounts/namespaces)
        or ignored (keys/members)."""
        for a in data.get("accounts", []):
            self.conn.execute(
                "INSERT OR REPLACE INTO accounts(id, name, email, display_name, avatar_url, type, "
                "install_id) VALUES (:id, :name, :email, :display_name, :avatar_url, "
                ":type, :install_id)",
                a,
            )
        for k in data.get("api_keys", []):
            self.conn.execute(
                "INSERT OR IGNORE INTO api_keys(key, account_id) VALUES (:key, :account_id)", k
            )
        for n in data.get("namespaces", []):
            self.conn.execute(
                "INSERT OR REPLACE INTO namespaces(name, account_id, featured, blacklisted) "
                "VALUES (:name, :account_id, :featured, :blacklisted)",
                n,
            )
        for m in data.get("members", []):
            self.conn.execute(
                "INSERT OR IGNORE INTO namespace_members(namespace, account_id, role) "
                "VALUES (:namespace, :account_id, :role)",
                m,
            )
        self.conn.commit()
        return {key: len(data.get(key, [])) for key in ("accounts", "api_keys", "namespaces", "members")}

    def reset_catalog(self, *, keep_auth: bool = True) -> None:
        """Wipe the catalog projection (modules/versions/facets/stars/reviews). The auth graph
        (accounts/api_keys/namespaces/members) is kept unless `keep_auth=False`. Does NOT touch
        artifact storage — clear the bucket/HF repo separately if a full reset is wanted."""
        catalog = ("reviews", "module_stars", "version_genes", "version_categories",
                   "versions", "modules")  # child → parent (FK-safe)
        for table in catalog:
            self.conn.execute(f"DELETE FROM {table}")
        if not keep_auth:
            for table in ("namespace_members", "api_keys", "namespaces", "accounts"):
                self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()

    def distinct_module_namespaces(self) -> list[str]:
        """Every namespace that has at least one published module. The source for classifying which
        namespaces are 'test/sandbox' when scoping the listing groups (works even for a namespace
        with no `namespaces` registry row, e.g. a seeded import)."""
        return [
            r["namespace"]
            for r in self.conn.execute(
                "SELECT DISTINCT namespace FROM modules ORDER BY namespace"
            ).fetchall()
        ]

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

    def delete_version(self, namespace: str, name: str, version: str) -> bool:
        """Hard-delete a single version (cascades its facet rows) and recompute the module's
        latest. Returns False if the module/version didn't exist. Ops-only — not the public API."""
        module = self.get_module_row(namespace, name)
        if module is None:
            return False
        cur = self.conn.execute(
            "DELETE FROM versions WHERE module_id = ? AND version = ?", (module["id"], version)
        )
        self.conn.commit()
        if cur.rowcount == 0:
            return False
        self.recompute_latest(int(module["id"]))
        return True

    def delete_namespace_grant(self, namespace: str) -> None:
        """Free a namespace's ownership + all memberships so a new key can claim it."""
        self.conn.execute("DELETE FROM namespaces WHERE name = ?", (namespace,))
        self.conn.execute("DELETE FROM namespace_members WHERE namespace = ?", (namespace,))
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
            # First publish: created_at and updated_at both take the stamp. Republishes advance
            # only updated_at (below), so created_at preserves the module's first-seen time.
            cur = self.conn.execute(
                "INSERT INTO modules(namespace, name, title, description, icon, color, "
                "genome_build, license, owner, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ident.namespace, ident.name, disp.title, disp.description, disp.icon,
                    disp.color, manifest.genome_build, manifest.license, manifest.owner,
                    updated_at, updated_at,
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

    def get_version_changelog(self, namespace: str, name: str, version: str) -> Optional[str]:
        """The version's changelog, or None if the version doesn't exist ('' is a valid value)."""
        row = self.conn.execute(
            "SELECT v.changelog FROM versions v JOIN modules m ON m.id = v.module_id "
            "WHERE m.namespace = ? AND m.name = ? AND v.version = ?",
            (namespace, name, version),
        ).fetchone()
        return row["changelog"] if row else None

    def set_version_changelog(
        self, namespace: str, name: str, version: str, changelog: str
    ) -> bool:
        """Amend a version's changelog (metadata only — never touches the artifact/digest)."""
        module = self.get_module_row(namespace, name)
        if module is None:
            return False
        cur = self.conn.execute(
            "UPDATE versions SET changelog = ? WHERE module_id = ? AND version = ?",
            (changelog, module["id"], version),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def list_all_versions(self, namespace: Optional[str] = None) -> list[sqlite3.Row]:
        """Every published version with its `(namespace, name, version)` — for ops-wide audits."""
        sql = (
            "SELECT m.namespace, m.name, v.version, v.manifest_json, v.needs_upgrade "
            "FROM versions v JOIN modules m ON m.id = v.module_id"
        )
        params: tuple[str, ...] = ()
        if namespace is not None:
            sql += " WHERE m.namespace = ?"
            params = (namespace,)
        sql += " ORDER BY m.namespace, m.name, v.version"
        return self.conn.execute(sql, params).fetchall()

    def set_needs_upgrade(
        self, namespace: str, name: str, version: str, needs_upgrade: bool
    ) -> bool:
        """Flag/unflag a version as failing the current contract (set by the `revalidate` audit).
        Non-destructive: the artifact/digest are untouched and the version stays fetchable."""
        module = self.get_module_row(namespace, name)
        if module is None:
            return False
        cur = self.conn.execute(
            "UPDATE versions SET needs_upgrade = ? WHERE module_id = ? AND version = ?",
            (int(needs_upgrade), module["id"], version),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def set_version_manifest(
        self, namespace: str, name: str, version: str, manifest: ModuleManifest
    ) -> bool:
        """Replace a version's stored manifest_json projection. Used by out-of-digest amendments
        (e.g. logo replacement) that keep `artifact.digest` — the content identity — unchanged."""
        module = self.get_module_row(namespace, name)
        if module is None:
            return False
        cur = self.conn.execute(
            "UPDATE versions SET manifest_json = ? WHERE module_id = ? AND version = ?",
            (manifest.model_dump_json(), module["id"], version),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def increment_downloads(self, namespace: str, name: str) -> None:
        self.conn.execute(
            "UPDATE modules SET downloads = downloads + 1 WHERE namespace = ? AND name = ?",
            (namespace, name),
        )
        self.conn.commit()

    def increment_version_downloads(self, namespace: str, name: str, version: str) -> None:
        self.conn.execute(
            "UPDATE versions SET downloads = downloads + 1 WHERE version = ? AND module_id = "
            "(SELECT id FROM modules WHERE namespace = ? AND name = ?)",
            (version, namespace, name),
        )
        self.conn.commit()

    def increment_views(self, namespace: str, name: str) -> None:
        self.conn.execute(
            "UPDATE modules SET views = views + 1 WHERE namespace = ? AND name = ?",
            (namespace, name),
        )
        self.conn.commit()

    def increment_search_hits(self, module_ids: list[int]) -> None:
        """Bump `search_hits` for every module that appeared in a search result page (one write)."""
        if not module_ids:
            return
        placeholders = ",".join("?" * len(module_ids))
        self.conn.execute(
            f"UPDATE modules SET search_hits = search_hits + 1 WHERE id IN ({placeholders})",
            module_ids,
        )
        self.conn.commit()

    # ── Stars (GitHub-style favourites; `module_stars` is truth, `modules.stars` a cache) ─────

    def star_module(self, module_id: int, account_id: int) -> bool:
        """Star a module for an account (idempotent). Returns True if this added a new star."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO module_stars(module_id, account_id) VALUES (?, ?)",
            (module_id, account_id),
        )
        if cur.rowcount:
            self.conn.execute(
                "UPDATE modules SET stars = stars + 1 WHERE id = ?", (module_id,)
            )
        self.conn.commit()
        return cur.rowcount > 0

    def unstar_module(self, module_id: int, account_id: int) -> bool:
        """Remove an account's star (idempotent). Returns True if a star was removed."""
        cur = self.conn.execute(
            "DELETE FROM module_stars WHERE module_id = ? AND account_id = ?",
            (module_id, account_id),
        )
        if cur.rowcount:
            self.conn.execute(
                "UPDATE modules SET stars = stars - 1 WHERE id = ?", (module_id,)
            )
        self.conn.commit()
        return cur.rowcount > 0

    def is_starred(self, module_id: int, account_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM module_stars WHERE module_id = ? AND account_id = ?",
            (module_id, account_id),
        ).fetchone()
        return row is not None

    # ── Reviews / audits (0.8.0) ────────────────────────────────────────────────

    def upsert_review(
        self,
        module_id: int,
        version: str,
        account_id: int,
        *,
        rating: int,
        verdict: Optional[str],
        notes: Optional[str],
        now: str,
    ) -> None:
        """Create or replace the caller's review of a specific version (one per account per version).
        Editing content leaves the owner's `highlighted` flag untouched (only the owner sets it)."""
        self.conn.execute(
            "INSERT INTO reviews(module_id, version, account_id, rating, verdict, notes, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(module_id, version, account_id) DO UPDATE SET "
            "rating = excluded.rating, verdict = excluded.verdict, notes = excluded.notes, "
            "updated_at = excluded.updated_at",
            (module_id, version, account_id, rating, verdict, notes, now, now),
        )
        self.conn.commit()

    def delete_review(self, module_id: int, version: str, account_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM reviews WHERE module_id = ? AND version = ? AND account_id = ?",
            (module_id, version, account_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def set_review_highlight(
        self, module_id: int, version: str, account_id: int, highlighted: bool
    ) -> bool:
        """Owner action: highlight/unhighlight one reviewer's review (SO accepted-answer style).
        Returns False if there is no such review."""
        cur = self.conn.execute(
            "UPDATE reviews SET highlighted = ? WHERE module_id = ? AND version = ? "
            "AND account_id = ?",
            (int(highlighted), module_id, version, account_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def list_reviews(self, module_id: int, version: Optional[str] = None) -> list[sqlite3.Row]:
        """Reviews for a module (optionally one version), highlighted first, then newest. Joins the
        reviewer's account name."""
        where = "r.module_id = ?"
        params: list[Any] = [module_id]
        if version is not None:
            where += " AND r.version = ?"
            params.append(version)
        return self.conn.execute(
            f"SELECT r.*, a.name AS reviewer FROM reviews r JOIN accounts a ON a.id = r.account_id "
            f"WHERE {where} ORDER BY r.highlighted DESC, r.updated_at DESC",
            params,
        ).fetchall()

    def review_summary(self, module_id: int) -> dict[str, Any]:
        """Module-level aggregate for the card: review count, average rating, and how many reviews
        the owner has highlighted (`curated` when > 0)."""
        row = self.conn.execute(
            "SELECT COUNT(*) AS n, AVG(rating) AS avg_rating, "
            "SUM(highlighted) AS highlighted FROM reviews WHERE module_id = ?",
            (module_id,),
        ).fetchone()
        count = int(row["n"])
        return {
            "review_count": count,
            "avg_rating": round(float(row["avg_rating"]), 2) if count else None,
            "highlighted_count": int(row["highlighted"] or 0),
        }

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
        namespace: Optional[str] = None,
        featured: Optional[bool] = None,
        include_blacklisted: bool = False,
        exclude_namespaces: Optional[list[str]] = None,
        only_namespaces: Optional[list[str]] = None,
        curated_only: bool = False,
        sort: str = "name",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[sqlite3.Row], int]:
        """Return (rows, total). Only modules with a current (non-yanked) latest are listed.

        Blacklisted namespaces are hidden unless `include_blacklisted` or a specific `namespace`
        filter is given. `featured` modules float to the top of every sort; `featured=True`
        restricts to them. `exclude_namespaces` / `only_namespaces` scope the result to a set of
        namespaces (used by the listing groups to hide or isolate test/sandbox spaces). Each row
        carries `featured`/`blacklisted` (from the namespaces table).
        """
        where: list[str] = ["m.latest_version IS NOT NULL"]
        params: list[Any] = []
        if not include_blacklisted and namespace is None:
            where.append("COALESCE(n.blacklisted, 0) = 0")
        if namespace:
            where.append("m.namespace = ?")
            params.append(namespace)
        if only_namespaces is not None:
            # An empty set means "nothing qualifies" (e.g. group=test with no test spaces) → no rows.
            if not only_namespaces:
                return [], 0
            where.append(f"m.namespace IN ({','.join('?' * len(only_namespaces))})")
            params.extend(only_namespaces)
        if exclude_namespaces:
            where.append(f"m.namespace NOT IN ({','.join('?' * len(exclude_namespaces))})")
            params.extend(exclude_namespaces)
        if curated_only:
            # Curated = at least one owner-highlighted review (the `curated` listing group).
            where.append("m.id IN (SELECT module_id FROM reviews WHERE highlighted = 1)")
        if featured:
            where.append("COALESCE(n.featured, 0) = 1")
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
        join = "LEFT JOIN namespaces n ON n.name = m.namespace"
        total = int(
            self.conn.execute(
                f"SELECT COUNT(*) AS n FROM modules m {join} WHERE {clause}", params
            ).fetchone()["n"]
        )
        rows = self.conn.execute(
            f"SELECT m.*, COALESCE(n.featured, 0) AS featured, "
            f"COALESCE(n.blacklisted, 0) AS blacklisted FROM modules m {join} "
            f"WHERE {clause} ORDER BY COALESCE(n.featured, 0) DESC, {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return rows, total

    # ── Moderation & key ops ────────────────────────────────────────────────

    def set_namespace_flags(
        self, namespace: str, *, featured: Optional[bool] = None, blacklisted: Optional[bool] = None
    ) -> bool:
        """Set featured/blacklisted on a namespace. Returns False if the namespace doesn't exist."""
        sets, params = [], []
        if featured is not None:
            sets.append("featured = ?")
            params.append(int(featured))
        if blacklisted is not None:
            sets.append("blacklisted = ?")
            params.append(int(blacklisted))
        if not sets:
            return self.namespace_owner(namespace) is not None
        params.append(namespace)
        cur = self.conn.execute(
            f"UPDATE namespaces SET {', '.join(sets)} WHERE name = ?", params
        )
        self.conn.commit()
        return cur.rowcount > 0

    def revoke_api_key(self, key: str) -> bool:
        cur = self.conn.execute("DELETE FROM api_keys WHERE key = ?", (key,))
        self.conn.commit()
        return cur.rowcount > 0

    def revoke_api_keys_for_account(self, name: str) -> int:
        row = self.account_by_name(name)
        if row is None:
            return 0
        cur = self.conn.execute("DELETE FROM api_keys WHERE account_id = ?", (row["id"],))
        self.conn.commit()
        return cur.rowcount
