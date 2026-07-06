"""
Admin CLI (Typer). Ops tasks that live outside the HTTP surface: run the server, initialize the
DB, and issue API keys / namespaces for the static-key auth model.
"""

import secrets

import typer
import uvicorn

from just_dna_marketplace.config import get_settings
from just_dna_marketplace.db.repository import Repository
from just_dna_marketplace.db.schema import connect, init_db

app = typer.Typer(help="just-dna-marketplace admin CLI", no_args_is_help=True)


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
def issue_key(account: str, namespace: list[str] = typer.Option([], "--namespace", "-n")) -> None:
    """Create an account (if needed), grant it namespaces, and print a fresh API key."""
    settings = get_settings()
    conn = connect(settings.db_path)
    init_db(conn)
    repo = Repository(conn)
    account_id = repo.create_account(account)
    for ns in namespace:
        repo.add_namespace(ns, account_id)
    key = "mk_live_" + secrets.token_urlsafe(24)
    repo.add_api_key(key, account_id)
    typer.echo(f"account={account} namespaces={namespace}")
    typer.echo(f"API key: {key}")


if __name__ == "__main__":
    app()
