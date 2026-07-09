"""Listing groups (0.8.0): server-owned namespace grouping behind the webui's tabs
(all / featured / popular / new / test). Membership is defined server-side so every client agrees;
test/sandbox namespaces (a config regex) are isolated to the `test` tab and hidden elsewhere."""

from fastapi.testclient import TestClient

from just_dna_registry.db.repository import Repository


def _seed_catalog(seed) -> None:
    common = dict(categories=["c"])
    seed("just-dna-seq", "coronary", "1.0.0", genes=["LPA"], created_at="2025-01-01T00:00:00Z", **common)
    seed("acme", "cancer", "1.0.0", genes=["BRCA1"], created_at="2025-02-01T00:00:00Z", **common)
    seed("sandbox-alice", "toy", "1.0.0", genes=["X"], created_at="2025-03-01T00:00:00Z", **common)
    seed("test-bob", "scratch", "1.0.0", genes=["Y"], created_at="2025-04-01T00:00:00Z", **common)


def _namespaces(body: dict) -> set[str]:
    return {i["namespace"] for i in body["items"]}


def test_default_listing_hides_test_and_sandbox(client: TestClient, seed) -> None:
    _seed_catalog(seed)
    ns = _namespaces(client.get("/api/v1/modules").json())
    assert {"just-dna-seq", "acme"} <= ns
    assert "sandbox-alice" not in ns and "test-bob" not in ns


def test_group_test_isolates_test_namespaces(client: TestClient, seed) -> None:
    _seed_catalog(seed)
    ns = _namespaces(client.get("/api/v1/modules", params={"group": "test"}).json())
    assert ns == {"sandbox-alice", "test-bob"}


def test_explicit_namespace_reaches_a_test_space(client: TestClient, seed) -> None:
    # A test/sandbox space is hidden from the tabs but still reachable by exact name.
    _seed_catalog(seed)
    body = client.get("/api/v1/modules", params={"namespace": "sandbox-alice"}).json()
    assert _namespaces(body) == {"sandbox-alice"}


def test_group_featured(client: TestClient, seed, app, api_key: str) -> None:
    _seed_catalog(seed)
    repo: Repository = app.state.repo
    assert repo.set_namespace_flags("just-dna-seq", featured=True)  # api_key fixture owns this ns
    body = client.get("/api/v1/modules", params={"group": "featured"}).json()
    assert _namespaces(body) == {"just-dna-seq"}


def test_group_popular_and_new_exclude_test(client: TestClient, seed) -> None:
    _seed_catalog(seed)
    for group in ("popular", "new"):
        ns = _namespaces(client.get("/api/v1/modules", params={"group": group}).json())
        assert "sandbox-alice" not in ns and "test-bob" not in ns, group
        assert {"just-dna-seq", "acme"} <= ns, group


def test_group_all_matches_default(client: TestClient, seed) -> None:
    _seed_catalog(seed)
    assert _namespaces(client.get("/api/v1/modules", params={"group": "all"}).json()) == _namespaces(
        client.get("/api/v1/modules").json()
    )


def test_groups_discovery_endpoint(client: TestClient) -> None:
    body = client.get("/api/v1/modules/groups").json()
    assert [g["key"] for g in body] == ["all", "featured", "curated", "popular", "new", "test"]
    assert all(g["label"] and g["description"] for g in body)


def test_invalid_group_rejected(client: TestClient) -> None:
    assert client.get("/api/v1/modules", params={"group": "bogus"}).status_code == 422
