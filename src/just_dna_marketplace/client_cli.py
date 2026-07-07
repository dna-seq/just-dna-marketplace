"""
`marketplace-client` — a test/ops CLI for the marketplace API.

Points at a running server via `--url` (or `$MARKETPLACE_URL`) and authenticates publish/update
with `--token` (or `$MARKETPLACE_TOKEN`). Commands: list, download, publish, find-by-hash,
update-module-version.
"""

import os
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from just_dna_format.identity import parse_version
from just_dna_format.manifest import read_manifest, write_manifest

from just_dna_marketplace.client import MarketplaceClient, MarketplaceError
from just_dna_marketplace.installid import generate_install_id

load_dotenv()  # pick up MARKETPLACE_URL / MARKETPLACE_TOKEN from a local .env

app = typer.Typer(help="Marketplace test client", no_args_is_help=True)

_URL_ENV = "MARKETPLACE_URL"
_TOKEN_ENV = "MARKETPLACE_TOKEN"


def _client(url: Optional[str], token: Optional[str], *, need_token: bool = False) -> MarketplaceClient:
    base = url or os.getenv(_URL_ENV) or "http://127.0.0.1:8000"
    tok = token or os.getenv(_TOKEN_ENV)
    if need_token and not tok:
        raise typer.BadParameter(f"a token is required (pass --token or set ${_TOKEN_ENV})")
    timeout = float(os.getenv("MARKETPLACE_TIMEOUT", "600"))  # big modules recompile for minutes
    return MarketplaceClient(base, tok, timeout=timeout)


UrlOpt = typer.Option(None, "--url", help=f"Marketplace base URL (or ${_URL_ENV})")
TokenOpt = typer.Option(None, "--token", help=f"API key for publish (or ${_TOKEN_ENV})")


@app.command("list")
def list_modules(
    q: Optional[str] = typer.Option(None, help="Full-text query"),
    gene: Optional[str] = typer.Option(None),
    category: Optional[str] = typer.Option(None),
    sort: str = typer.Option("name", help="downloads|recent|name"),
    url: Optional[str] = UrlOpt,
) -> None:
    """List / search catalog modules."""
    with _client(url, None) as c:
        body = c.list_modules(q=q, gene=gene, category=category, sort=sort)
    typer.echo(f"{body['total']} module(s):")
    for item in body["items"]:
        typer.echo(
            f"  {item['namespace']}/{item['name']}@{item['latest_version']}"
            f"  [{item['stats']['variant_count']} variants, {item['stats']['gene_count']} genes]"
            f"  ↓{item['downloads']}  — {item['title']}"
        )


@app.command()
def download(
    namespace: str,
    name: str,
    version: str,
    dest: Path = typer.Argument(..., help="Directory to extract into, or the .tar.gz path with --tarball"),
    tarball: bool = typer.Option(False, "--tarball", help="Fetch a single streamable tar.gz instead"),
    url: Optional[str] = UrlOpt,
) -> None:
    """Download a version's artifact (+ logs): verify-then-install, or a single tar.gz."""
    with _client(url, None) as c:
        if tarball:
            path = c.get_tarball(namespace, name, version, dest)
            typer.echo(f"✓ downloaded {namespace}/{name}@{version} → {path}")
            return
        manifest = c.download(namespace, name, version, dest)
    typer.echo(f"✓ downloaded + verified {namespace}/{name}@{version} → {dest}")
    typer.echo(f"  digest {manifest.artifact.digest}")
    if manifest.logs:
        typer.echo(f"  logs: {', '.join(e.name for e in manifest.logs)}")


@app.command("import-module")
def import_module(
    namespace: str,
    name: str,
    version: str,
    archive: Path = typer.Argument(..., help="A zip/tar.gz spec archive (or legacy parquet-only)"),
    changelog: str = typer.Option("", "--changelog"),
    title: Optional[str] = typer.Option(None, help="Display metadata for legacy parquet-only imports"),
    description: Optional[str] = typer.Option(None),
    report_title: Optional[str] = typer.Option(None),
    icon: Optional[str] = typer.Option(None),
    color: Optional[str] = typer.Option(None),
    url: Optional[str] = UrlOpt,
    token: Optional[str] = TokenOpt,
) -> None:
    """Publish a module from a zip/tar.gz archive (in-house packaging / legacy import)."""
    display = {
        "title": title, "description": description, "report_title": report_title,
        "icon": icon, "color": color,
    }
    with _client(url, token, need_token=True) as c:
        manifest = c.import_module(namespace, name, version, archive, changelog=changelog, display=display)
    typer.echo(f"✓ imported {manifest.identity.canonical_id}  digest {manifest.artifact.digest}")


@app.command()
def publish(
    namespace: str,
    name: str,
    version: str,
    spec_dir: Path = typer.Argument(..., help="Spec directory (module_spec.yaml + CSVs [+ logs])"),
    changelog: str = typer.Option("", "--changelog"),
    url: Optional[str] = UrlOpt,
    token: Optional[str] = TokenOpt,
) -> None:
    """Publish a spec as a new module version (server-side recompile)."""
    with _client(url, token, need_token=True) as c:
        manifest = c.publish(namespace, name, version, spec_dir, changelog)
    # Stamp the published identity into the local spec dir so it's discernible as "published-by-me".
    write_manifest(manifest, Path(spec_dir) / "manifest.json")
    typer.echo(f"✓ published {manifest.identity.canonical_id}")
    typer.echo(f"  digest {manifest.artifact.digest}  compile_success={manifest.compilation.compile_success}")
    typer.echo(f"  stamped {spec_dir}/manifest.json (identity + published_at)")


@app.command()
def register(
    account: str,
    install_id: Optional[str] = typer.Option(
        None, "--install-id", help="Existing install-id; omit to grind a fresh one"
    ),
    difficulty: int = typer.Option(20, help="Proof-of-work bits when generating an install-id"),
    url: Optional[str] = UrlOpt,
) -> None:
    """Self-register an account from an install-id (proof-of-work) and print an API key."""
    if not install_id:
        typer.echo(f"grinding install-id (difficulty {difficulty})…")
        install_id = generate_install_id(difficulty)
    with _client(url, None) as c:
        result = c.register(install_id, account)
    typer.echo(f"✓ registered account={result['account']} namespaces={result['namespaces']}")
    typer.echo(f"install-id: {install_id}")
    typer.echo(f"API key: {result['token']}")


@app.command("namespace-available")
def namespace_available(namespace: str, url: Optional[str] = UrlOpt) -> None:
    """Check whether a namespace is free to claim."""
    with _client(url, None) as c:
        info = c.namespace_available(namespace)
    state = "available" if info["available"] else "taken"
    valid = "" if info["valid"] else " (invalid name)"
    typer.echo(f"{namespace}: {state}{valid}")


@app.command("claim-namespace")
def claim_namespace(
    namespace: str, url: Optional[str] = UrlOpt, token: Optional[str] = TokenOpt
) -> None:
    """Claim an available namespace for your account (token)."""
    with _client(url, token, need_token=True) as c:
        result = c.claim_namespace(namespace)
    note = " (already yours)" if result.get("already_owned") else ""
    typer.echo(f"✓ {result['namespace']} → owner {result['owner']}{note}")


@app.command("find-by-hash")
def find_by_hash(
    digest: Optional[str] = typer.Argument(None, help="sha256:… artifact digest"),
    manifest_path: Optional[Path] = typer.Option(
        None, "--manifest", help="Read the digest from a local manifest.json instead"
    ),
    url: Optional[str] = UrlOpt,
) -> None:
    """Check whether an artifact digest is already published (dedup / provenance check)."""
    if manifest_path is not None:
        digest = read_manifest(manifest_path).artifact.digest
    if not digest:
        raise typer.BadParameter("provide a DIGEST or --manifest")
    with _client(url, None) as c:
        matches = c.lookup_by_digest(digest)
    if not matches:
        typer.echo(f"not published: {digest}")
        raise typer.Exit(code=1)
    typer.echo(f"{len(matches)} match(es) for {digest}:")
    for m in matches:
        flag = " (yanked)" if m["yanked"] else ""
        typer.echo(f"  {m['namespace']}/{m['name']}@{m['version']}{flag}")


@app.command("amend-changelog")
def amend_changelog(
    namespace: str,
    name: str,
    version: str,
    changelog: str,
    append: bool = typer.Option(False, "--append", help="Append to the existing changelog"),
    url: Optional[str] = UrlOpt,
    token: Optional[str] = TokenOpt,
) -> None:
    """Amend a published version's changelog (metadata only; the artifact stays immutable)."""
    with _client(url, token, need_token=True) as c:
        result = c.amend_changelog(namespace, name, version, changelog, append=append)
    typer.echo(f"✓ {namespace}/{name}@{version} changelog updated:\n{result['changelog']}")


@app.command("amend-logo")
def amend_logo(
    namespace: str,
    name: str,
    version: str,
    logo: Path = typer.Argument(..., help="Logo image (png/jpg/jpeg)"),
    url: Optional[str] = UrlOpt,
    token: Optional[str] = TokenOpt,
) -> None:
    """Replace a published version's logo (metadata only; out of the digest, so no version bump)."""
    with _client(url, token, need_token=True) as c:
        result = c.amend_logo(namespace, name, version, logo)
    logo_entry = result.get("logo") or {}
    typer.echo(f"✓ {namespace}/{name}@{version} logo updated → {logo_entry.get('name')}")


@app.command("update-module-version")
def update_module_version(
    namespace: str,
    name: str,
    version: str,
    spec_dir: Path = typer.Argument(..., help="Updated spec directory"),
    changelog: str = typer.Option("", "--changelog"),
    url: Optional[str] = UrlOpt,
    token: Optional[str] = TokenOpt,
) -> None:
    """Publish a higher version of an existing module (checks it supersedes the current latest)."""
    with _client(url, token, need_token=True) as c:
        try:
            detail = c.get_module(namespace, name)
        except MarketplaceError as exc:
            if exc.status_code == 404:
                raise typer.BadParameter(
                    f"{namespace}/{name} does not exist yet — use `publish` for the first version"
                )
            raise
        latest = detail.get("latest_version")
        if latest and parse_version(version) <= parse_version(latest):
            raise typer.BadParameter(f"version {version} must be greater than current latest {latest}")
        manifest = c.publish(namespace, name, version, spec_dir, changelog)
    typer.echo(f"✓ updated {namespace}/{name}: {latest} → {manifest.identity.version}")


if __name__ == "__main__":
    app()
