"""Read/catalog + download endpoint contract tests (SPEC §8.1–§8.5, §13)."""

from typing import Callable

import pytest
from fastapi.testclient import TestClient
from just_dna_module.integrity import IntegrityError, verify_manifest
from just_dna_module.manifest import ModuleManifest


@pytest.fixture
def seeded(seed: Callable[..., ModuleManifest]) -> None:
    seed("just-dna-seq", "longevity_variants_2026", "1.0.0",
         genes=["CGAS", "TERT"], categories=["cGAS-STING pathway"],
         created_at="2025-01-01T00:00:00Z")
    seed("just-dna-seq", "longevity_variants_2026", "2.0.0",
         genes=["CGAS", "TERT", "SIRT1"], categories=["cGAS-STING pathway"],
         created_at="2025-06-01T00:00:00Z")
    seed("just-dna-seq", "coronary", "1.0.0",
         genes=["LPA"], categories=["cardio"], created_at="2025-03-01T00:00:00Z")


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_list_empty(client: TestClient) -> None:
    body = client.get("/api/v1/modules").json()
    assert body == {"items": [], "total": 0, "page": 1, "per_page": 20}


def test_list_returns_cards(client: TestClient, seeded: None) -> None:
    body = client.get("/api/v1/modules").json()
    assert body["total"] == 2
    names = {item["name"] for item in body["items"]}
    assert names == {"longevity_variants_2026", "coronary"}
    longevity = next(i for i in body["items"] if i["name"] == "longevity_variants_2026")
    assert longevity["latest_version"] == "2.0.0"  # highest non-yanked SemVer
    assert longevity["stats"]["gene_count"] == 3
    assert len(longevity["stats"]["genes"]) <= 3  # card genes truncated


def test_search_by_gene_facet(client: TestClient, seeded: None) -> None:
    body = client.get("/api/v1/modules", params={"gene": "LPA"}).json()
    assert [i["name"] for i in body["items"]] == ["coronary"]


def test_search_by_category_and_q(client: TestClient, seeded: None) -> None:
    assert client.get("/api/v1/modules", params={"category": "cardio"}).json()["total"] == 1
    assert client.get("/api/v1/modules", params={"q": "coronary"}).json()["total"] == 1
    assert client.get("/api/v1/modules", params={"q": "nomatch"}).json()["total"] == 0


def test_sort_recent(client: TestClient, seeded: None) -> None:
    items = client.get("/api/v1/modules", params={"sort": "recent"}).json()["items"]
    # longevity's latest ingest (2025-06) is newer than coronary (2025-03).
    assert items[0]["name"] == "longevity_variants_2026"


def test_detail_and_404(client: TestClient, seeded: None) -> None:
    assert client.get("/api/v1/modules/just-dna-seq/missing").status_code == 404
    detail = client.get("/api/v1/modules/just-dna-seq/longevity_variants_2026").json()
    assert {v["version"] for v in detail["versions"]} == {"1.0.0", "2.0.0"}
    assert detail["latest_manifest"]["identity"]["version"] == "2.0.0"
    assert detail["stats"]["gene_count"] == 3  # full/from latest manifest


def test_versions_endpoint(client: TestClient, seeded: None) -> None:
    body = client.get("/api/v1/modules/just-dna-seq/longevity_variants_2026/versions").json()
    assert body["total"] == 2
    assert body["items"][0]["manifest_url"].endswith("/manifest")


def test_manifest_fetch_and_404(client: TestClient, seeded: None) -> None:
    ok = client.get("/api/v1/modules/just-dna-seq/coronary/versions/1.0.0/manifest")
    assert ok.status_code == 200
    assert ok.json()["compilation"]["compiled_by"] == "marketplace-server"
    missing = client.get("/api/v1/modules/just-dna-seq/coronary/versions/9.9.9/manifest")
    assert missing.status_code == 404


def test_download_and_integrity_roundtrip(
    client: TestClient, seeded: None, tmp_path
) -> None:
    base = "/api/v1/modules/just-dna-seq/coronary/versions/1.0.0"
    listing = client.get(f"{base}/download").json()
    assert {f["name"] for f in listing["files"]} == {
        "weights.parquet", "annotations.parquet", "studies.parquet"
    }
    # Download each file, reconstruct the module dir, and verify against the manifest.
    module_dir = tmp_path / "install"
    module_dir.mkdir()
    for f in listing["files"]:
        data = client.get(f"{base}/files/{f['name']}").content
        (module_dir / f["name"]).write_bytes(data)
    manifest = ModuleManifest.model_validate(
        client.get(f"{base}/manifest").json()
    )
    verify_manifest(module_dir, manifest)  # passes on untampered install

    # Tamper one byte -> verification fails.
    (module_dir / "weights.parquet").write_bytes(b"corrupted")
    with pytest.raises(IntegrityError):
        verify_manifest(module_dir, manifest)


def test_download_increments_counter(client: TestClient, seeded: None) -> None:
    base = "/api/v1/modules/just-dna-seq/coronary/versions/1.0.0/download"
    client.get(base)
    client.get(base)
    card = next(
        i for i in client.get("/api/v1/modules").json()["items"] if i["name"] == "coronary"
    )
    assert card["downloads"] == 2
