"""Archive-import tests against real sample module zips in data/input (gitignored).

Skipped when the sample zips are absent (they're not committed). Exercises the full path:
zip upload → server-side recompile → catalog → tarball download.
"""

import io
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

INPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "input"


def _zip(name: str) -> Path:
    path = INPUT_DIR / name
    if not path.is_file():
        pytest.skip(f"sample zip not present: {name}")
    return path


def _spec_name(zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path) as zf:
        member = next(n for n in zf.namelist() if n.endswith("module_spec.yaml"))
        return yaml.safe_load(zf.read(member))["module"]["name"]


def _import(client: TestClient, key: str, name: str, zip_path: Path, version: str = "1.0.0"):
    return client.post(
        f"/api/v1/modules/just-dna-seq/{name}/versions/import",
        data={"version": version, "changelog": "import"},
        files={"archive": (zip_path.name, zip_path.read_bytes(), "application/zip")},
        headers={"Authorization": f"Bearer {key}"},
    )


@pytest.mark.parametrize(
    "zip_name",
    ["diabetes_metabolism_v2.zip", "longevity_2025_v4.zip", "multimorbidity_aging_v1.zip"],
)
def test_import_valid_spec_zip(client: TestClient, api_key: str, zip_name: str) -> None:
    zip_path = _zip(zip_name)
    name = _spec_name(zip_path)
    resp = _import(client, api_key, name, zip_path)
    assert resp.status_code == 201, resp.text
    manifest = resp.json()
    assert manifest["identity"]["name"] == name
    assert manifest["identity"]["canonical_id"] == f"just-dna-seq/{name}@1.0.0"
    assert manifest["compilation"]["compiled_by"] == "marketplace-server"
    assert manifest["stats"]["variant_count"] > 0
    # Appears in the catalog.
    listing = client.get("/api/v1/modules").json()
    assert any(i["name"] == name for i in listing["items"])


def test_import_captures_bundled_logs(client: TestClient, api_key: str) -> None:
    zip_path = _zip("longevity_variants_2026_v2.zip")  # ships a v2.log
    name = _spec_name(zip_path)
    resp = _import(client, api_key, name, zip_path)
    assert resp.status_code == 201, resp.text
    log_names = {e["name"] for e in resp.json()["logs"]}
    assert any(n.endswith(".log") for n in log_names), log_names
    # The bundled log is fetchable via the files endpoint.
    log = sorted(log_names)[0]
    got = client.get(f"/api/v1/modules/just-dna-seq/{name}/versions/1.0.0/files/{log}")
    assert got.status_code == 200 and got.content


def test_import_missing_studies_rejected(client: TestClient, api_key: str) -> None:
    zip_path = _zip("putter_v1.zip")  # no studies.csv -> fails mandatory grounding
    name = _spec_name(zip_path)
    resp = _import(client, api_key, name, zip_path)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_spec"


def test_import_name_mismatch_rejected(client: TestClient, api_key: str) -> None:
    zip_path = _zip("longevity_2025_v4.zip")
    resp = _import(client, api_key, "wrong_name", zip_path)  # path name != spec name
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "name_mismatch"


def test_import_then_tarball_download(client: TestClient, api_key: str) -> None:
    zip_path = _zip("diabetes_metabolism_v2.zip")
    name = _spec_name(zip_path)
    assert _import(client, api_key, name, zip_path).status_code == 201

    resp = client.get(
        f"/api/v1/modules/just-dna-seq/{name}/versions/1.0.0/download", params={"format": "tarball"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/gzip"
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        members = set(tar.getnames())
    assert "manifest.json" in members
    assert "weights.parquet" in members
