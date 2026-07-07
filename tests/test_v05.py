"""0.5.0 accommodation of just-dna-format 0.2.0: structured provenance, gene-panel + icon_set +
ClinVar-stat surfacing, module logo (served, in card, amend without version bump), and optional
Ed25519 signing (publish signs, /pubkey serves, client verifies a pinned key)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from just_dna_format.integrity import IntegrityError, verify_manifest
from just_dna_format.manifest import ModuleManifest
from just_dna_format.signing import generate_private_key_pem, public_key_b64_from_pem

from just_dna_marketplace.api.app import create_app
from just_dna_marketplace.config import Settings
from just_dna_marketplace.db.repository import Repository

_YAML = """\
schema_version: "1.0"
module:
  name: cardio
  title: Cardio
  description: d
  report_title: R
  icon: heartbeat
  icon_set: awesome
  color: "#21ba45"
defaults:
  curator: t
  method: m
genome_build: GRCh38
panel:
  source: clinvar
  reference: "2026-06"
  genes: [BRCA1, BRCA2]
  significance: [pathogenic, likely_pathogenic]
"""
_VARIANTS = (
    "rsid,chrom,start,ref,alts,genotype,weight,state,conclusion,negatives,gene,category,clinvar,pathogenic,benign\n"
    "rs4244285,10,94781859,G,A,A/G,-0.8,risk,het,a trade-off,CYP2C19,cyp2c19,true,true,false\n"
)
_STUDIES = "rsid,pmid,population,p_value,conclusion,study_design\nrs4244285,[PMID: 29165669],T,0.05,E,U\n"
_PROVENANCE = json.dumps(
    {"generator": "agent-x", "model": "claude", "agent_version": "1.0",
     "items": [{"variant_key": "rs4244285", "rationale": "curated", "human_reviewed": True}]}
).encode()
_LOGO = b"\x89PNG\r\n\x1a\n cardio-logo"
_BASE = "/api/v1/modules/just-dna-seq/cardio/versions/1.0.0"


def _files(*, logo: bool = True, provenance: bool = True) -> list:
    files = [
        ("files", ("module_spec.yaml", _YAML.encode(), "text/yaml")),
        ("files", ("variants.csv", _VARIANTS.encode(), "text/csv")),
        ("files", ("studies.csv", _STUDIES.encode(), "text/csv")),
    ]
    if provenance:
        files.append(("files", ("provenance.json", _PROVENANCE, "application/json")))
    if logo:
        files.append(("files", ("logo.png", _LOGO, "image/png")))
    return files


def _publish(client: TestClient, key: str, **kw) -> dict:
    resp = client.post(
        "/api/v1/modules/just-dna-seq/cardio/versions",
        data={"version": "1.0.0"},
        files=_files(**kw),
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _signing_app(tmp_path: Path):
    """A fresh app configured to sign, plus a usable owner key. Returns (app, api_key, pem)."""
    pem = generate_private_key_pem()
    key_path = tmp_path / "signing_key.pem"
    key_path.write_bytes(pem)
    settings = Settings(
        db_path=tmp_path / "m.db",
        storage_backend="local",
        local_storage_dir=tmp_path / "artifacts",
        signing_key=key_path,
    )
    app = create_app(settings)
    repo: Repository = app.state.repo
    account_id = repo.create_account("antonkulaga")
    repo.add_namespace("just-dna-seq", account_id)
    repo.add_api_key("mk_live_testkey", account_id)
    return app, "mk_live_testkey", pem


# ── Provenance / panel / icon_set / clinvar / logo surfacing ────────────────────


def test_publish_carries_provenance_panel_and_stats(client: TestClient, api_key: str) -> None:
    manifest = _publish(client, api_key)
    assert manifest["provenance"]["item_count"] == 1
    assert manifest["provenance"]["generator"] == "agent-x"
    assert manifest["panel"]["source"] == "clinvar"
    assert manifest["panel"]["genes"] == ["BRCA1", "BRCA2"]
    assert manifest["display"]["icon_set"] == "awesome"
    assert manifest["stats"]["clinvar_count"] == 1
    assert manifest["stats"]["pathogenic_count"] == 1
    assert manifest["logo"]["name"] == "logo.png"


def test_provenance_and_logo_are_served(client: TestClient, api_key: str) -> None:
    _publish(client, api_key)
    assert client.get(f"{_BASE}/files/provenance.json").content == _PROVENANCE
    assert client.get(f"{_BASE}/files/logo.png").content == _LOGO


def test_detail_card_surfaces_logo_and_stats(client: TestClient, api_key: str) -> None:
    _publish(client, api_key)
    card = client.get("/api/v1/modules/just-dna-seq/cardio").json()
    assert card["icon_set"] == "awesome"
    assert card["logo_url"] == f"{_BASE}/files/logo.png"
    assert card["stats"]["clinvar_count"] == 1
    assert card["stats"]["pathogenic_count"] == 1


def test_tarball_includes_logo(client: TestClient, api_key: str) -> None:
    import io
    import tarfile

    _publish(client, api_key)
    resp = client.get(f"{_BASE}/download", params={"format": "tarball"})
    with tarfile.open(fileobj=io.BytesIO(resp.content)) as tar:
        assert "logo.png" in tar.getnames()


# ── Logo amendment (out of digest, no version bump) ─────────────────────────────


def test_amend_logo_keeps_digest_and_version(client: TestClient, api_key: str) -> None:
    published = _publish(client, api_key)
    digest_before = published["artifact"]["digest"]
    new_logo = b"\xff\xd8\xff new-jpeg-logo"  # jpg bytes

    resp = client.post(
        f"{_BASE}/logo",
        files={"logo": ("logo.jpg", new_logo, "image/jpeg")},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["logo"]["name"] == "logo.jpg"

    manifest = client.get(f"{_BASE}/manifest").json()
    assert manifest["logo"]["name"] == "logo.jpg"
    assert manifest["artifact"]["digest"] == digest_before  # content identity unchanged
    assert client.get(f"{_BASE}/files/logo.jpg").content == new_logo

    # Still exactly one version — no bump.
    versions = client.get("/api/v1/modules/just-dna-seq/cardio/versions").json()
    assert [v["version"] for v in versions["items"]] == ["1.0.0"]


def test_amend_logo_rejects_bad_extension(client: TestClient, api_key: str) -> None:
    _publish(client, api_key)
    resp = client.post(
        f"{_BASE}/logo",
        files={"logo": ("logo.gif", b"GIF89a", "image/gif")},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert resp.status_code == 422, resp.text


# ── Optional Ed25519 signing ────────────────────────────────────────────────────


def test_publish_signs_and_pubkey_matches(tmp_path: Path) -> None:
    app, key, pem = _signing_app(tmp_path)
    client = TestClient(app)
    manifest = _publish(client, key)
    assert manifest["signature"] is not None
    assert manifest["signature"]["algorithm"] == "ed25519"

    pub = client.get("/api/v1/pubkey").json()
    assert pub["public_key"] == public_key_b64_from_pem(pem)
    assert manifest["signature"]["public_key"] == pub["public_key"]

    versions = client.get("/api/v1/modules/just-dna-seq/cardio/versions").json()
    assert versions["items"][0]["signed"] is True


def test_pubkey_404_when_unsigned(client: TestClient) -> None:
    assert client.get("/api/v1/pubkey").status_code == 404


def _download_to(client: TestClient, dest: Path) -> ModuleManifest:
    """Fetch a version's files + manifest into `dest` (what the reference client does)."""
    dest.mkdir(parents=True, exist_ok=True)
    listing = client.get(f"{_BASE}/download").json()
    manifest = ModuleManifest.model_validate(client.get(f"{_BASE}/manifest").json())
    names = [f["name"] for f in listing["files"]]
    names += [e["name"] for e in client.get(f"{_BASE}/logs").json()["items"]]
    if manifest.logo is not None:
        names.append(manifest.logo.name)
    if manifest.provenance is not None and manifest.provenance.file:
        names.append(manifest.provenance.file)
    for rel in names:
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(client.get(f"{_BASE}/files/{rel}").content)
    return manifest


def test_served_signed_module_verifies_with_pinned_key(tmp_path: Path) -> None:
    app, key, pem = _signing_app(tmp_path)
    client = TestClient(app)
    _publish(client, key)

    good = client.get("/api/v1/pubkey").json()["public_key"]
    assert good == public_key_b64_from_pem(pem)
    bad = public_key_b64_from_pem(generate_private_key_pem())

    manifest = _download_to(client, tmp_path / "dl")
    # The served bytes + served pubkey verify together; a different pinned key is rejected.
    verify_manifest(
        tmp_path / "dl", manifest,
        check_logs=True, check_logo=True, check_provenance=True, public_key=good,
    )
    with pytest.raises(IntegrityError):
        verify_manifest(tmp_path / "dl", manifest, public_key=bad)


def test_amend_logo_preserves_signature(tmp_path: Path) -> None:
    app, key, pem = _signing_app(tmp_path)
    client = TestClient(app)
    published = _publish(client, key)
    sig_before = published["signature"]["signature"]

    resp = client.post(
        f"{_BASE}/logo",
        files={"logo": ("logo.jpg", b"\xff\xd8\xff jpeg", "image/jpeg")},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 200, resp.text
    manifest = client.get(f"{_BASE}/manifest").json()
    # Signature is over artifact.digest, which the logo swap doesn't touch → still valid, unchanged.
    assert manifest["signature"]["signature"] == sig_before
