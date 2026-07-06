"""Logs-over-API, generalized file serving, and digest lookup (SPEC §8 extensions)."""

from fastapi.testclient import TestClient

_MODULE_YAML = """\
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
_STUDIES = "rsid,pmid,population,p_value,conclusion,study_design\nrs4244285,1,T,0.05,E,U\n"
_RUN_LOG = b"aggregate run transcript\n"
_REVIEWER_LOG = b"reviewer verdict\n"


def _publish_with_logs(client: TestClient, key: str) -> dict:
    resp = client.post(
        "/api/v1/modules/just-dna-seq/coronary/versions",
        data={"version": "1.0.0"},
        files=[
            ("files", ("module_spec.yaml", _MODULE_YAML.encode(), "text/yaml")),
            ("files", ("variants.csv", _VARIANTS.encode(), "text/csv")),
            ("files", ("studies.csv", _STUDIES.encode(), "text/csv")),
            ("files", ("run.log", _RUN_LOG, "text/plain")),
            ("files", ("logs/reviewer.log", _REVIEWER_LOG, "text/plain")),
        ],
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_logs_listed_and_served(client: TestClient, api_key: str) -> None:
    manifest = _publish_with_logs(client, api_key)
    assert {e["name"] for e in manifest["logs"]} == {"run.log", "logs/reviewer.log"}

    base = "/api/v1/modules/just-dna-seq/coronary/versions/1.0.0"
    logs = client.get(f"{base}/logs").json()["items"]
    assert {e["name"] for e in logs} == {"run.log", "logs/reviewer.log"}

    # The generalized files endpoint serves a nested log by its manifest path.
    assert client.get(f"{base}/files/logs/reviewer.log").content == _REVIEWER_LOG
    assert client.get(f"{base}/files/run.log").content == _RUN_LOG
    # And still serves artifact parquets + inputs, but rejects unknown files.
    assert client.get(f"{base}/files/weights.parquet").status_code == 200
    assert client.get(f"{base}/files/variants.csv").status_code == 200
    assert client.get(f"{base}/files/nope.txt").status_code == 404


def test_lookup_by_digest(client: TestClient, api_key: str) -> None:
    manifest = _publish_with_logs(client, api_key)
    digest = manifest["artifact"]["digest"]
    matches = client.get("/api/v1/modules/lookup", params={"digest": digest}).json()["matches"]
    assert matches == [{"namespace": "just-dna-seq", "name": "coronary", "version": "1.0.0",
                        "yanked": False}]
    # Unknown digest -> empty (not an error).
    miss = client.get("/api/v1/modules/lookup", params={"digest": "sha256:deadbeef"}).json()
    assert miss["matches"] == []


def test_logs_endpoint_empty_when_none(client: TestClient, api_key: str, seed) -> None:
    seed("just-dna-seq", "coronary", "1.0.0", genes=["LPA"], categories=["cardio"],
         created_at="2025-03-01T00:00:00Z")  # seeded module has no logs
    logs = client.get("/api/v1/modules/just-dna-seq/coronary/versions/1.0.0/logs").json()
    assert logs["items"] == []
