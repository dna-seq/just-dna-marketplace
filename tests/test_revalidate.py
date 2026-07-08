"""Contract-drift audit: `revalidate` finds published versions that fail the current contract,
sets a non-destructive `needs_upgrade` flag, and never touches the immutable artifact."""

from fastapi.testclient import TestClient
from just_dna_format.manifest import ModuleManifest

from just_dna_marketplace.db.repository import Repository
from just_dna_marketplace.services.revalidate import gather_pmids, revalidate_version
from just_dna_marketplace.storage.base import version_key

_YAML = """\
schema_version: "1.0"
module:
  name: coronary
  title: Coronary
  description: d
  report_title: R
genome_build: GRCh38
"""
# A 0.3-complete spec (carries direction/stat_significance) so revalidation is a clean "ok" — the
# legacy state-only case is exercised in test_upgrade.py's "upgradable" path.
_VARIANTS = (
    "rsid,chrom,start,ref,alts,genotype,weight,state,conclusion,gene,category,direction,stat_significance\n"
    "rs4244285,10,94781859,G,A,A/G,-0.8,risk,het,CYP2C19,cyp2c19,risk,significant\n"
)
_STUDIES = "rsid,pmid,population,p_value,conclusion,study_design\nrs4244285,[PMID: 29165669],T,0.05,E,U\n"
_BASE = "/api/v1/modules/just-dna-seq/coronary/versions/1.0.0"


def _publish(client: TestClient, key: str) -> ModuleManifest:
    resp = client.post(
        "/api/v1/modules/just-dna-seq/coronary/versions",
        data={"version": "1.0.0"},
        files=[
            ("files", ("module_spec.yaml", _YAML.encode(), "text/yaml")),
            ("files", ("variants.csv", _VARIANTS.encode(), "text/csv")),
            ("files", ("studies.csv", _STUDIES.encode(), "text/csv")),
        ],
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 201, resp.text
    return ModuleManifest.model_validate(resp.json())


def test_revalidate_ok_and_pmids(client: TestClient, api_key: str, app) -> None:
    manifest = _publish(client, api_key)
    status, errors = revalidate_version(app.state.storage, "just-dna-seq", "coronary", "1.0.0", manifest)
    assert status == "ok", errors
    assert gather_pmids(app.state.storage, "just-dna-seq", "coronary", "1.0.0", manifest) == ["29165669"]


def test_revalidate_flags_drifted_version(client: TestClient, api_key: str, app) -> None:
    manifest = _publish(client, api_key)
    repo: Repository = app.state.repo

    # Simulate a version published under a laxer contract: overwrite its stored studies.csv with a
    # non-PMID reference the *current* validator rejects. (The artifact/digest are untouched.)
    bad = "rsid,pmid,population,p_value,conclusion,study_design\nrs4244285,https://www.ncbi.nlm.nih.gov/snp/rs4244285,T,0.05,E,U\n"
    app.state.storage.store_module(version_key("just-dna-seq", "coronary", "1.0.0"), {"studies.csv": bad.encode()})

    status, errors = revalidate_version(app.state.storage, "just-dna-seq", "coronary", "1.0.0", manifest)
    assert status == "needs_upgrade"
    assert errors

    assert repo.set_needs_upgrade("just-dna-seq", "coronary", "1.0.0", True)
    versions = client.get("/api/v1/modules/just-dna-seq/coronary/versions").json()
    assert versions["items"][0]["needs_upgrade"] is True
    # Non-destructive: the artifact digest is unchanged and the version still lists.
    assert versions["items"][0]["artifact_digest"] == manifest.artifact.digest


def test_revalidate_skips_when_inputs_missing_from_storage(client: TestClient, api_key: str, app) -> None:
    # Manifest lists inputs, but the bytes aren't retrievable → skip, never a false needs_upgrade.
    manifest = _publish(client, api_key)
    app.state.storage.remove(version_key("just-dna-seq", "coronary", "1.0.0"))
    status, _ = revalidate_version(app.state.storage, "just-dna-seq", "coronary", "1.0.0", manifest)
    assert status == "skipped"


def test_revalidate_skips_when_no_inputs(seed, app) -> None:
    manifest = seed(
        "just-dna-seq", "coronary", "1.0.0", genes=["LPA"], categories=["cardio"],
        created_at="2025-03-01T00:00:00Z",
    )  # seeded manifests carry no inputs[] → cannot be revalidated
    status, _ = revalidate_version(app.state.storage, "just-dna-seq", "coronary", "1.0.0", manifest)
    assert status == "skipped"
