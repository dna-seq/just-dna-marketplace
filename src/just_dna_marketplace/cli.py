"""
Admin CLI (Typer). Ops tasks that live outside the HTTP surface: run the server, initialize the
DB, and issue API keys / namespaces for the static-key auth model.
"""

import secrets

import httpx
import typer
import uvicorn
from just_dna_format.manifest import ModuleManifest

from just_dna_marketplace.config import Settings, get_settings
from just_dna_marketplace.db.repository import Repository
from just_dna_marketplace.db.schema import connect, init_db
from just_dna_marketplace.models.api import VALID_ACCOUNT_TYPES
from just_dna_marketplace.services.pmid_check import verify_pmids
from just_dna_marketplace.services.revalidate import gather_pmids, revalidate_version
from just_dna_marketplace.services.upgrade import plan_version_upgrade, upgrade_version
from just_dna_marketplace.storage.base import StorageBackend
from just_dna_marketplace.storage.local import LocalStorage

app = typer.Typer(help="just-dna-marketplace admin CLI", no_args_is_help=True)


def _storage(settings: Settings) -> StorageBackend:
    if settings.storage_backend == "local":
        return LocalStorage(settings.local_storage_dir)
    if settings.storage_backend == "hf":
        from just_dna_marketplace.storage.hf import HfStorage  # imports huggingface_hub lazily

        return HfStorage(settings.hf_repo_id, token=settings.hf_token)
    raise typer.BadParameter(f"unsupported storage_backend {settings.storage_backend!r}")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Run the API server."""
    uvicorn.run("just_dna_marketplace.api.app:app", host=host, port=port, reload=reload)


@app.command("init-db")
def init_db_command() -> None:
    """Create the catalog tables if they do not exist."""
    settings = get_settings()
    conn = connect(settings.db_path)
    init_db(conn)
    typer.echo(f"Initialized catalog DB at {settings.db_path}")


@app.command("issue-key")
def issue_key(
    account: str,
    namespace: list[str] = typer.Option([], "--namespace", "-n"),
    email: str = typer.Option(None, "--email", help="Account contact email (private)"),
    display_name: str = typer.Option(None, "--display-name", help="Human display name"),
    account_type: str = typer.Option("user", "--type", help="Account type: user|org"),
) -> None:
    """Create an account (if needed), grant it namespaces, and print a fresh API key."""
    if account_type not in VALID_ACCOUNT_TYPES:
        raise typer.BadParameter(f"--type must be one of {sorted(VALID_ACCOUNT_TYPES)}")
    settings = get_settings()
    conn = connect(settings.db_path)
    init_db(conn)
    repo = Repository(conn)
    account_id = repo.create_account(account)
    repo.set_account_type(account_id, account_type)
    if email is not None or display_name is not None:
        repo.set_account_profile(account_id, email=email, display_name=display_name)
    for ns in namespace:
        repo.add_namespace(ns, account_id)
    key = "mk_live_" + secrets.token_urlsafe(24)
    repo.add_api_key(key, account_id)
    typer.echo(f"account={account} type={account_type} namespaces={namespace}")
    typer.echo(f"API key: {key}")


@app.command("add-member")
def add_member(
    namespace: str,
    account: str,
    role: str = typer.Option("contributor", "--role", "-r", help="owner | contributor"),
) -> None:
    """Add or promote an account in a namespace (ops). Both roles publish; only owners manage."""
    if role not in ("owner", "contributor"):
        raise typer.BadParameter("role must be 'owner' or 'contributor'")
    settings = get_settings()
    repo = Repository(connect(settings.db_path))
    if repo.namespace_owner(namespace) is None:
        typer.echo(f"namespace not found: {namespace}")
        raise typer.Exit(code=1)
    row = repo.account_by_name(account)
    if row is None:
        typer.echo(f"account not found: {account}")
        raise typer.Exit(code=1)
    repo.add_member(namespace, int(row["id"]), role)
    typer.echo(f"{namespace}: {account} is now {role}")


@app.command("remove-member")
def remove_member(namespace: str, account: str) -> None:
    """Revoke an account's membership in a namespace (ops). Refuses to remove the last owner."""
    settings = get_settings()
    repo = Repository(connect(settings.db_path))
    row = repo.account_by_name(account)
    if row is None:
        typer.echo(f"account not found: {account}")
        raise typer.Exit(code=1)
    account_id = int(row["id"])
    if (
        repo.namespace_role(namespace, account_id) == "owner"
        and repo.count_namespace_owners(namespace) <= 1
    ):
        typer.echo(f"refusing to remove the last owner of {namespace}")
        raise typer.Exit(code=1)
    if not repo.remove_member(namespace, account_id):
        typer.echo(f"{account} is not a member of {namespace}")
        raise typer.Exit(code=1)
    typer.echo(f"{namespace}: removed {account}")


@app.command("list-members")
def list_members(namespace: str) -> None:
    """List a namespace's members and their roles (ops)."""
    settings = get_settings()
    repo = Repository(connect(settings.db_path))
    members = repo.list_members(namespace)
    if not members:
        typer.echo(f"{namespace}: no members (namespace may not exist)")
        return
    for m in members:
        typer.echo(f"  {m['role']:<12} {m['account']}")


@app.command("remove-module")
def remove_module(
    namespace: str, name: str, yes: bool = typer.Option(False, "--yes", "-y")
) -> None:
    """Hard-delete a module (all versions + artifacts). Ops-only; not reversible, not yank."""
    settings = get_settings()
    repo = Repository(connect(settings.db_path))
    storage = _storage(settings)
    if not yes:
        typer.confirm(f"Hard-delete {namespace}/{name} and ALL its artifacts?", abort=True)
    versions = repo.delete_module(namespace, name)
    storage.remove(f"{namespace}/{name}")
    typer.echo(f"removed {namespace}/{name} ({len(versions)} version(s): {versions})")


@app.command("remove-version")
def remove_version(
    namespace: str, name: str, version: str, yes: bool = typer.Option(False, "--yes", "-y")
) -> None:
    """Hard-delete a single version + its artifacts (not yank). Frees it for re-upload."""
    settings = get_settings()
    repo = Repository(connect(settings.db_path))
    storage = _storage(settings)
    if not yes:
        typer.confirm(f"Hard-delete {namespace}/{name}@{version} and its artifacts?", abort=True)
    if not repo.delete_version(namespace, name, version):
        typer.echo(f"not found: {namespace}/{name}@{version}")
        raise typer.Exit(code=1)
    storage.remove(f"{namespace}/{name}/{version}")
    typer.echo(f"removed {namespace}/{name}@{version}")


@app.command("remove-namespace")
def remove_namespace(namespace: str, yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    """Hard-delete every module under a namespace + its artifacts, and free the namespace so a new
    key can claim it. Ops-only; nothing resurfaces."""
    settings = get_settings()
    repo = Repository(connect(settings.db_path))
    storage = _storage(settings)
    modules = repo.modules_in_namespace(namespace)
    if not yes:
        typer.confirm(
            f"Hard-delete namespace {namespace!r}: {len(modules)} module(s) + artifacts, "
            "and free the namespace?",
            abort=True,
        )
    for module in modules:
        repo.delete_module(namespace, module["name"])
    repo.delete_namespace_grant(namespace)
    storage.remove(namespace)  # nuke any residual {ns}/ subtree
    typer.echo(
        f"removed namespace {namespace}: {len(modules)} module(s) purged; namespace freed"
    )


def _set_flag(namespace: str, *, featured=None, blacklisted=None) -> None:
    settings = get_settings()
    repo = Repository(connect(settings.db_path))
    if not repo.set_namespace_flags(namespace, featured=featured, blacklisted=blacklisted):
        typer.echo(f"namespace not found: {namespace}")
        raise typer.Exit(code=1)
    typer.echo(f"{namespace}: featured={featured} blacklisted={blacklisted}")


@app.command()
def feature(namespace: str) -> None:
    """Mark a namespace featured (floats to the top of listings)."""
    _set_flag(namespace, featured=True)


@app.command()
def unfeature(namespace: str) -> None:
    """Clear a namespace's featured flag."""
    _set_flag(namespace, featured=False)


@app.command()
def blacklist(namespace: str) -> None:
    """Hide a namespace from default listings/search (still reachable by direct request)."""
    _set_flag(namespace, blacklisted=True)


@app.command()
def unblacklist(namespace: str) -> None:
    """Un-hide a blacklisted namespace."""
    _set_flag(namespace, blacklisted=False)


@app.command()
def revalidate(
    namespace: str = typer.Option(None, "--namespace", "-n", help="Limit to one namespace"),
    set_flag: bool = typer.Option(
        False, "--set-flag/--report-only",
        help="Set the needs_upgrade flag on failing versions (default: report only)",
    ),
    check_pmids: bool = typer.Option(
        False, "--check-pmids", help="Also verify each study PMID resolves at NCBI (network)"
    ),
) -> None:
    """Re-run the current contract's `validate_spec` over every published version's stored spec.

    Finds modules that a `just-dna-format` bump would now reject. Published artifacts are immutable
    and untouched; with `--set-flag` failing versions are marked `needs_upgrade` so listings surface
    them and an upgrade (re-publish as a new PATCH) can be scheduled. See docs/UPGRADE.md."""
    settings = get_settings()
    conn = connect(settings.db_path)
    init_db(conn)  # idempotent: ensures the needs_upgrade column exists on a pre-0.5.0 DB
    repo = Repository(conn)
    storage = _storage(settings)
    counts = {"ok": 0, "upgradable": 0, "needs_upgrade": 0, "skipped": 0}
    for row in repo.list_all_versions(namespace):
        ns, name, ver = row["namespace"], row["name"], row["version"]
        manifest = ModuleManifest.model_validate_json(row["manifest_json"])
        status, messages = revalidate_version(storage, ns, name, ver, manifest)

        pmid_note = ""
        if check_pmids:
            pmids = gather_pmids(storage, ns, name, ver, manifest)
            try:
                missing = [p for p, exists in verify_pmids(pmids).items() if not exists]
            except httpx.HTTPError as exc:
                pmid_note = f"  [pmid check failed: {exc}]"
            else:
                if missing:
                    status = "needs_upgrade"
                    messages = [*messages, f"PMIDs not found at NCBI: {', '.join(missing)}"]
                pmid_note = f"  [{len(pmids)} pmid(s) checked]"

        counts[status] += 1
        marker = {"ok": "✓", "upgradable": "⇧", "needs_upgrade": "✗", "skipped": "–"}[status]
        typer.echo(f"{marker} {ns}/{name}@{ver} [{status}]{pmid_note}")
        for msg in messages[:5]:
            typer.echo(f"    {msg}")
        # Both a validation failure and a 0.3 back-population are "re-publish me" states.
        if set_flag and status in ("ok", "upgradable", "needs_upgrade"):
            repo.set_needs_upgrade(ns, name, ver, status in ("upgradable", "needs_upgrade"))

    typer.echo(
        f"\n{counts['ok']} ok, {counts['upgradable']} upgradable, "
        f"{counts['needs_upgrade']} needs_upgrade, {counts['skipped']} skipped"
        + ("" if set_flag else "  (report only; pass --set-flag to persist)")
    )


@app.command()
def upgrade(
    namespace: str = typer.Option(None, "--namespace", "-n", help="Limit to one namespace"),
    module: str = typer.Option(None, "--module", "-m", help="Limit to one module name"),
    apply: bool = typer.Option(
        False, "--apply/--dry-run",
        help="Actually re-publish upgraded versions (default: dry-run, report only)",
    ),
) -> None:
    """Back-populate the additive 0.3 axes (direction/stat_significance/clin_sig) and re-publish.

    For every published version whose `variants.csv` still carries only the legacy `state`/ClinVar
    booleans, applies the format's `VariantRow.upgraded()` derivation and — with `--apply` —
    re-publishes the result as the next PATCH through the normal server-side compile path. The
    predecessor is never mutated and stays fetchable. Dry-run by default. See docs/UPGRADE.md."""
    settings = get_settings()
    conn = connect(settings.db_path)
    init_db(conn)
    repo = Repository(conn)
    storage = _storage(settings)
    planned = upgraded = 0
    for row in repo.list_all_versions(namespace):
        ns, name, ver = row["namespace"], row["name"], row["version"]
        if module is not None and name != module:
            continue
        manifest = ModuleManifest.model_validate_json(row["manifest_json"])
        if not apply:
            plan = plan_version_upgrade(storage, ns, name, ver, manifest)
            if plan is not None and plan.needed:
                planned += 1
                typer.echo(f"⇧ {ns}/{name}@{ver}: {plan.upgradable_rows}/{plan.total_rows} row(s) "
                           f"would upgrade → next PATCH")
            continue
        result = upgrade_version(
            repo=repo, storage=storage, settings=settings,
            namespace=ns, name=name, version=ver, manifest=manifest,
        )
        if result is not None:
            new_version, _ = result
            upgraded += 1
            typer.echo(f"✓ {ns}/{name}@{ver} → {new_version} (0.3 upgrade published)")

    if apply:
        typer.echo(f"\n{upgraded} version(s) upgraded and re-published")
    else:
        typer.echo(f"\n{planned} version(s) would upgrade  (dry-run; pass --apply to publish)")


@app.command("revoke-key")
def revoke_key(key: str) -> None:
    """Invalidate a single API key (e.g. a leaked one)."""
    settings = get_settings()
    repo = Repository(connect(settings.db_path))
    typer.echo("revoked" if repo.revoke_api_key(key) else "no such key")


@app.command("revoke-account")
def revoke_account(account: str, yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    """Invalidate ALL API keys for an account."""
    settings = get_settings()
    repo = Repository(connect(settings.db_path))
    if not yes:
        typer.confirm(f"Revoke all API keys for account {account!r}?", abort=True)
    typer.echo(f"revoked {repo.revoke_api_keys_for_account(account)} key(s)")


if __name__ == "__main__":
    app()
