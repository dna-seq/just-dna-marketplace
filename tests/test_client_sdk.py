"""RegistryClient SDK (0.8.1) — real, in-process coverage of the full REST surface it now wraps
(identity/profile, members, yank, stars, reviews, groups, aggregate stats).

The SDK is synchronous (the CLI depends on that), and a sync httpx client can't drive an async
ASGI transport — so each call is bridged onto a worker thread with `asyncio.to_thread` while the
FastAPI app is driven in-process through Starlette's ASGI transport. Real routers, DB, and auth —
no stubbed HTTP layer."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from just_dna_registry.client import RegistryClient

_NS, _NAME, _VER = "just-dna-seq", "coronary", "1.0.0"


@pytest.fixture
def sdk(app, api_key):
    """A real RegistryClient bound to the in-process app; token = the namespace-owner account."""
    tc = TestClient(app)
    client = RegistryClient(
        "http://testserver", token=api_key, transport=tc._transport, check_version=False
    )
    try:
        yield client
    finally:
        client.close()


def _seed_module(seed, name: str = _NAME, genes=("LPA",), created_at="2025-01-01T00:00:00Z"):
    return seed(_NS, name, _VER, genes=list(genes), categories=["cardio"], created_at=created_at)


async def test_whoami_and_profile(sdk) -> None:
    who = await asyncio.to_thread(sdk.whoami)
    assert who["account"] == "antonkulaga" and who["type"] == "user"
    updated = await asyncio.to_thread(
        lambda: sdk.update_profile(display_name="Anton", avatar_url="https://x/a.png")
    )
    assert updated["display_name"] == "Anton" and updated["avatar_url"] == "https://x/a.png"
    assert (await asyncio.to_thread(sdk.whoami))["display_name"] == "Anton"


async def test_members_roundtrip(sdk, app) -> None:
    app.state.repo.create_account("bob")
    roster = await asyncio.to_thread(lambda: sdk.add_member(_NS, "bob", "contributor"))
    assert {m["account"] for m in roster["members"]} >= {"antonkulaga", "bob"}
    listed = await asyncio.to_thread(lambda: sdk.members(_NS))
    assert any(m["account"] == "bob" for m in listed)
    after = await asyncio.to_thread(lambda: sdk.remove_member(_NS, "bob"))
    assert all(m["account"] != "bob" for m in after["members"])


async def test_stars(sdk, seed) -> None:
    _seed_module(seed)
    starred = await asyncio.to_thread(lambda: sdk.star(_NS, _NAME))
    assert starred["stars"] == 1 and starred["starred_by_me"] is True
    unstarred = await asyncio.to_thread(lambda: sdk.unstar(_NS, _NAME))
    assert unstarred["stars"] == 0


async def test_reviews_highlight_and_curated_stat(sdk, seed) -> None:
    _seed_module(seed)
    posted = await asyncio.to_thread(
        lambda: sdk.review(_NS, _NAME, _VER, rating=5, verdict="verified")
    )
    assert posted[0]["rating"] == 5 and posted[0]["highlighted"] is False
    highlighted = await asyncio.to_thread(
        lambda: sdk.highlight_review(_NS, _NAME, _VER, "antonkulaga")
    )
    assert highlighted[0]["highlighted"] is True
    # The highlight is what the `curated` group/stat keys on.
    curated = await asyncio.to_thread(lambda: sdk.catalog_stats(group="curated"))
    assert curated["curated"] == 1
    assert await asyncio.to_thread(lambda: sdk.delete_review(_NS, _NAME, _VER)) == []


async def test_yank_unyank(sdk, seed) -> None:
    _seed_module(seed)
    assert (await asyncio.to_thread(lambda: sdk.yank(_NS, _NAME, _VER)))["yanked"] is True
    assert (await asyncio.to_thread(lambda: sdk.unyank(_NS, _NAME, _VER)))["yanked"] is False


async def test_groups_and_catalog_stats(sdk, seed) -> None:
    _seed_module(seed)
    _seed_module(seed, name="cardio2", genes=("APOB", "PCSK9"), created_at="2025-02-01T00:00:00Z")
    keys = [g["key"] for g in await asyncio.to_thread(sdk.groups)]
    assert keys[0] == "all" and "curated" in keys
    stats = await asyncio.to_thread(sdk.catalog_stats)
    assert stats["modules"] >= 2 and stats["genes"] >= 3 and stats["namespaces"] == 1
