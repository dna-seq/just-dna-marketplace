"""Auth, publish (server-side recompile), and yank contract tests (SPEC §8.6–§8.9, §13)."""

from typing import Callable

from fastapi.testclient import TestClient
from just_dna_format.integrity import verify_manifest
from just_dna_format.manifest import ModuleManifest

# A self-contained spec: variants carry positions, so publish compiles with resolve_with_ensembl
# off (the app fixture's default) — no Ensembl reference needed.
_MODULE_YAML = """\
schema_version: "1.0"
module:
  name: {name}
  title: Coronary
  description: Coronary artery disease risk
  report_title: Coronary
  icon: heart
  color: "#db2828"
genome_build: GRCh38
"""
_VARIANTS = (
    "rsid,chrom,start,ref,alts,genotype,weight,state,conclusion,gene,category\n"
    "rs4244285,10,94781859,G,A,A/G,-0.8,risk,het,CYP2C19,cyp2c19\n"
)
_STUDIES = "rsid,pmid,population,p_value,conclusion,study_design\nrs4244285,1,T,0.05,E,U\n"


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _spec_files(name: str, *, with_studies: bool = True) -> list[tuple]:
    files = [
        ("files", ("module_spec.yaml", _MODULE_YAML.format(name=name).encode(), "text/yaml")),
        ("files", ("variants.csv", _VARIANTS.encode(), "text/csv")),
    ]
    if with_studies:
        files.append(("files", ("studies.csv", _STUDIES.encode(), "text/csv")))
    return files


def _publish(client, key, namespace, name, version, *, with_studies=True):
    return client.post(
        f"/api/v1/modules/{namespace}/{name}/versions",
        data={"version": version, "changelog": "initial"},
        files=_spec_files(name, with_studies=with_studies),
        headers=_auth(key),
    )


# ── Auth ────────────────────────────────────────────────────────────────────


def test_whoami_requires_token(client: TestClient) -> None:
    assert client.get("/api/v1/auth/whoami").status_code == 401
    assert client.get("/api/v1/auth/whoami", headers=_auth("bogus")).status_code == 401


def test_whoami_ok(client: TestClient, api_key: str) -> None:
    body = client.get("/api/v1/auth/whoami", headers=_auth(api_key)).json()
    assert body == {
        "account": "antonkulaga", "namespaces": ["just-dna-seq"],
        "type": "user", "display_name": None, "avatar_url": None, "email": None,
    }


# ── Publish guards ────────────────────────────────────────────────────────────


def test_publish_requires_auth(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/modules/just-dna-seq/coronary/versions",
        data={"version": "1.0.0"},
        files=_spec_files("coronary"),
    )
    assert resp.status_code == 401


def test_publish_rejects_unowned_namespace(client: TestClient, api_key: str) -> None:
    resp = _publish(client, api_key, "someone-else", "coronary", "1.0.0")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "not_namespace_member"


def test_publish_rejects_bad_version(client: TestClient, api_key: str) -> None:
    resp = _publish(client, api_key, "just-dna-seq", "coronary", "not-semver")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "invalid_version"


def test_publish_rejects_existing_version(
    client: TestClient, api_key: str, seed: Callable[..., ModuleManifest]
) -> None:
    seed("just-dna-seq", "coronary", "1.0.0", genes=["LPA"], categories=["cardio"],
         created_at="2025-03-01T00:00:00Z")
    resp = _publish(client, api_key, "just-dna-seq", "coronary", "1.0.0")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "version_exists"


def test_publish_invalid_spec_returns_422(client: TestClient, api_key: str) -> None:
    # Missing the mandatory studies.csv -> rejected before/at validation.
    resp = _publish(client, api_key, "just-dna-seq", "coronary", "1.0.0", with_studies=False)
    assert resp.status_code == 422
    assert "detail" in resp.json()


def test_publish_name_mismatch_returns_422(client: TestClient, api_key: str) -> None:
    # module_spec.yaml says name=coronary but the path says lipids.
    resp = client.post(
        "/api/v1/modules/just-dna-seq/lipids/versions",
        data={"version": "1.0.0"},
        files=_spec_files("coronary"),
        headers=_auth(api_key),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "name_mismatch"


# ── Publish end-to-end (server-side recompile) ────────────────────────────────


def test_publish_compiles_indexes_and_serves(client: TestClient, api_key: str, tmp_path) -> None:
    resp = _publish(client, api_key, "just-dna-seq", "coronary", "1.0.0")
    assert resp.status_code == 201, resp.text
    manifest = resp.json()
    assert manifest["identity"]["canonical_id"] == "just-dna-seq/coronary@1.0.0"
    assert manifest["owner"] == "antonkulaga"
    assert manifest["compilation"]["compile_success"] is True
    assert manifest["compilation"]["compiled_by"] == "marketplace-server"
    assert manifest["stats"]["genes"] == ["CYP2C19"]

    # It now appears in the catalog with the right latest version.
    listing = client.get("/api/v1/modules").json()
    card = next(i for i in listing["items"] if i["name"] == "coronary")
    assert card["latest_version"] == "1.0.0"

    # Download + integrity-verify the compiled artifact end to end.
    base = "/api/v1/modules/just-dna-seq/coronary/versions/1.0.0"
    listing = client.get(f"{base}/download").json()
    module_dir = tmp_path / "install"
    module_dir.mkdir()
    for f in listing["files"]:
        (module_dir / f["name"]).write_bytes(client.get(f"{base}/files/{f['name']}").content)
    full = ModuleManifest.model_validate(client.get(f"{base}/manifest").json())
    verify_manifest(module_dir, full)  # digests match + trusted compiled_by


def test_publish_immutability_second_time_409(client: TestClient, api_key: str) -> None:
    assert _publish(client, api_key, "just-dna-seq", "coronary", "1.0.0").status_code == 201
    assert _publish(client, api_key, "just-dna-seq", "coronary", "1.0.0").status_code == 409


# ── Yank ──────────────────────────────────────────────────────────────────────


def test_yank_hides_version_from_latest(
    client: TestClient, api_key: str, seed: Callable[..., ModuleManifest]
) -> None:
    seed("just-dna-seq", "longevity_variants_2026", "1.0.0", genes=["CGAS"],
         categories=["x"], created_at="2025-01-01T00:00:00Z")
    seed("just-dna-seq", "longevity_variants_2026", "2.0.0", genes=["CGAS"],
         categories=["x"], created_at="2025-06-01T00:00:00Z")
    base = "/api/v1/modules/just-dna-seq/longevity_variants_2026"
    assert client.post(f"{base}/versions/2.0.0/yank", headers=_auth(api_key)).json()["yanked"] is True
    assert client.get(base).json()["latest_version"] == "1.0.0"
    client.post(f"{base}/versions/2.0.0/yank", json={"yanked": False}, headers=_auth(api_key))
    assert client.get(base).json()["latest_version"] == "2.0.0"


def test_yank_unknown_version_404(client: TestClient, api_key: str) -> None:
    resp = client.post(
        "/api/v1/modules/just-dna-seq/ghost/versions/1.0.0/yank", headers=_auth(api_key)
    )
    assert resp.status_code == 404
