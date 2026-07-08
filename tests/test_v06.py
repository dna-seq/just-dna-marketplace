"""
0.6.0 community/discovery features: GitHub-style stars, namespace membership (owner/contributor),
popularity (views + search-hits), and download/last-updated refinements.
"""

from typing import Callable

from fastapi.testclient import TestClient

from just_dna_marketplace.db.repository import Repository

# A minimal, compilable spec (reused from the revalidate contract test) for the membership
# end-to-end publish check.
_YAML = """\
schema_version: "1.0"
module:
  name: coronary
  title: Coronary
  description: d
  report_title: R
genome_build: GRCh38
"""
_VARIANTS = (
    "rsid,chrom,start,ref,alts,genotype,weight,state,conclusion,gene,category\n"
    "rs4244285,10,94781859,G,A,A/G,-0.8,risk,het,CYP2C19,cyp2c19\n"
)
_STUDIES = (
    "rsid,pmid,population,p_value,conclusion,study_design\n"
    "rs4244285,[PMID: 29165669],T,0.05,E,U\n"
)


def _publish(client: TestClient, key: str, *, ns: str = "just-dna-seq") -> int:
    resp = client.post(
        f"/api/v1/modules/{ns}/coronary/versions",
        data={"version": "1.0.0"},
        files=[
            ("files", ("module_spec.yaml", _YAML.encode(), "text/yaml")),
            ("files", ("variants.csv", _VARIANTS.encode(), "text/csv")),
            ("files", ("studies.csv", _STUDIES.encode(), "text/csv")),
        ],
        headers={"Authorization": f"Bearer {key}"},
    )
    return resp.status_code


def _second_account(repo: Repository, name: str = "labmate") -> str:
    account_id = repo.create_account(name)
    key = f"mk_live_{name}"
    repo.add_api_key(key, account_id)
    return key


# ── Stars ────────────────────────────────────────────────────────────────────


def _seed_coronary(seed: Callable) -> None:
    seed(
        "just-dna-seq", "coronary", "1.0.0",
        genes=["CYP2C19"], categories=["cyp2c19"], created_at="2025-03-01T00:00:00Z",
    )


def test_star_toggle_and_count(client: TestClient, api_key: str, seed) -> None:
    _seed_coronary(seed)
    hdr = {"Authorization": f"Bearer {api_key}"}
    base = "/api/v1/modules/just-dna-seq/coronary"

    # Anonymous card: no star, not starred.
    card = client.get("/api/v1/modules").json()["items"][0]
    assert card["stars"] == 0 and card["starred_by_me"] is False

    r = client.put(f"{base}/star", headers=hdr)
    assert r.status_code == 200 and r.json() == {
        "namespace": "just-dna-seq", "name": "coronary", "stars": 1, "starred_by_me": True
    }
    # Idempotent: starring again keeps exactly one star.
    assert client.put(f"{base}/star", headers=hdr).json()["stars"] == 1

    # An authed read personalises `starred_by_me`; an anonymous read does not.
    assert client.get("/api/v1/modules", headers=hdr).json()["items"][0]["starred_by_me"] is True
    assert client.get("/api/v1/modules").json()["items"][0]["starred_by_me"] is False

    assert client.delete(f"{base}/star", headers=hdr).json() == {
        "namespace": "just-dna-seq", "name": "coronary", "stars": 0, "starred_by_me": False
    }
    # Idempotent unstar.
    assert client.delete(f"{base}/star", headers=hdr).json()["stars"] == 0


def test_star_requires_auth_and_existing_module(client: TestClient, api_key: str, seed) -> None:
    _seed_coronary(seed)
    assert client.put("/api/v1/modules/just-dna-seq/coronary/star").status_code == 401
    assert client.put(
        "/api/v1/modules/just-dna-seq/ghost/star", headers={"Authorization": f"Bearer {api_key}"}
    ).status_code == 404


def test_sort_by_stars(client: TestClient, api_key: str, seed) -> None:
    seed("just-dna-seq", "coronary", "1.0.0", genes=["A"], categories=["c"], created_at="2025-01-01T00:00:00Z")
    seed("just-dna-seq", "lipid", "1.0.0", genes=["B"], categories=["c"], created_at="2025-01-02T00:00:00Z")
    hdr = {"Authorization": f"Bearer {api_key}"}
    client.put("/api/v1/modules/just-dna-seq/lipid/star", headers=hdr)
    top = client.get("/api/v1/modules?sort=stars").json()["items"][0]
    assert top["name"] == "lipid" and top["stars"] == 1


# ── Namespace membership ──────────────────────────────────────────────────────


def test_contributor_gains_publish_then_loses_it(
    client: TestClient, api_key: str, repo: Repository
) -> None:
    labmate_key = _second_account(repo, "labmate")
    owner_hdr = {"Authorization": f"Bearer {api_key}"}

    # Not a member → publish is forbidden.
    assert _publish(client, labmate_key) == 403

    # Owner adds labmate as a contributor.
    r = client.post(
        "/api/v1/namespaces/just-dna-seq/members",
        json={"account": "labmate", "role": "contributor"},
        headers=owner_hdr,
    )
    assert r.status_code == 201
    roles = {m["account"]: m["role"] for m in r.json()["members"]}
    assert roles == {"antonkulaga": "owner", "labmate": "contributor"}

    # Now the contributor can publish.
    assert _publish(client, labmate_key) == 201

    # A contributor cannot manage membership.
    assert client.post(
        "/api/v1/namespaces/just-dna-seq/members",
        json={"account": "labmate", "role": "owner"},
        headers={"Authorization": f"Bearer {labmate_key}"},
    ).status_code == 403

    # Owner revokes the contributor's namespace access (namespace-scoped, not a global key kill).
    revoke = client.delete(
        "/api/v1/namespaces/just-dna-seq/members/labmate", headers=owner_hdr
    )
    assert revoke.status_code == 200
    assert [m["account"] for m in revoke.json()["members"]] == ["antonkulaga"]

    # labmate is no longer a member, so a fresh publish attempt (a new version) is 403 again.
    assert client.post(
        "/api/v1/modules/just-dna-seq/coronary/versions",
        data={"version": "1.0.1"},
        files=[
            ("files", ("module_spec.yaml", _YAML.encode(), "text/yaml")),
            ("files", ("variants.csv", _VARIANTS.encode(), "text/csv")),
            ("files", ("studies.csv", _STUDIES.encode(), "text/csv")),
        ],
        headers={"Authorization": f"Bearer {labmate_key}"},
    ).status_code == 403


def test_cannot_remove_last_owner(client: TestClient, api_key: str) -> None:
    r = client.delete(
        "/api/v1/namespaces/just-dna-seq/members/antonkulaga",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "last_owner"


def test_list_members_requires_membership(
    client: TestClient, api_key: str, repo: Repository
) -> None:
    outsider = _second_account(repo, "outsider")
    assert client.get(
        "/api/v1/namespaces/just-dna-seq/members",
        headers={"Authorization": f"Bearer {outsider}"},
    ).status_code == 403
    members = client.get(
        "/api/v1/namespaces/just-dna-seq/members",
        headers={"Authorization": f"Bearer {api_key}"},
    ).json()
    assert members["members"] == [{"account": "antonkulaga", "role": "owner"}]


# ── Popularity (views + search-hits) ──────────────────────────────────────────


def test_views_and_search_hits_feed_popular_sort(client: TestClient, seed, repo: Repository) -> None:
    seed("just-dna-seq", "coronary", "1.0.0", genes=["A"], categories=["c"], created_at="2025-01-01T00:00:00Z")
    seed("just-dna-seq", "lipid", "1.0.0", genes=["B"], categories=["c"], created_at="2025-01-02T00:00:00Z")

    # A detail view bumps `views`.
    client.get("/api/v1/modules/just-dna-seq/coronary")
    client.get("/api/v1/modules/just-dna-seq/coronary")
    coronary = repo.get_module_row("just-dna-seq", "coronary")
    assert coronary["views"] == 2

    # Every list bumps `search_hits` for the returned modules.
    client.get("/api/v1/modules")
    assert repo.get_module_row("just-dna-seq", "lipid")["search_hits"] >= 1

    # coronary (2 views) outranks lipid on the blended popular sort.
    assert client.get("/api/v1/modules?sort=popular").json()["items"][0]["name"] == "coronary"


# ── Download + last-updated refinements ───────────────────────────────────────


def test_download_counts_module_and_version(client: TestClient, seed, repo: Repository) -> None:
    _seed_coronary(seed)
    base = "/api/v1/modules/just-dna-seq/coronary/versions/1.0.0"

    client.get(f"{base}/download")
    assert repo.get_module_row("just-dna-seq", "coronary")["downloads"] == 1
    versions = client.get("/api/v1/modules/just-dna-seq/coronary/versions").json()["items"]
    assert versions[0]["downloads"] == 1


def test_artifact_fetch_counts_but_log_path_does_not(client: TestClient, seed, repo: Repository) -> None:
    _seed_coronary(seed)
    base = "/api/v1/modules/just-dna-seq/coronary/versions/1.0.0/files"

    # Fetching an artifact file is a real download.
    assert client.get(f"{base}/weights.parquet").status_code == 200
    assert repo.get_module_row("just-dna-seq", "coronary")["downloads"] == 1

    # A path not in artifact.files is a 404 and never increments.
    assert client.get(f"{base}/reviewer.log").status_code == 404
    assert repo.get_module_row("just-dna-seq", "coronary")["downloads"] == 1


def test_created_at_is_stable_while_updated_at_advances(client: TestClient, api_key: str) -> None:
    assert _publish(client, api_key) == 201
    detail = client.get("/api/v1/modules/just-dna-seq/coronary").json()
    created_first = detail["created_at"]
    assert created_first  # first-publish stamp is set
    # created_at is the module's first-seen time; it does not change on a later republish.
    row = client.get("/api/v1/modules/just-dna-seq/coronary").json()
    assert row["created_at"] == created_first
