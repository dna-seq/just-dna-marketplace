"""0.3 contract upgrade: back-populate the additive 0.3 axes (direction/stat_significance/clin_sig)
from the legacy `state`/booleans and re-publish as a new PATCH. The `revalidate` audit surfaces such
versions as `upgradable` (they still validate — the columns are additive); `upgrade_version`
performs the migrate + re-publish, never mutating the predecessor."""

import csv
import io

from fastapi.testclient import TestClient
from just_dna_format.manifest import ModuleManifest

from just_dna_marketplace.config import Settings
from just_dna_marketplace.services.revalidate import revalidate_version
from just_dna_marketplace.services.upgrade import (
    is_latest_version,
    plan_variants_upgrade,
    upgrade_version,
)
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
# Legacy spec: only `state`, no 0.3 columns.
_VARIANTS = (
    "rsid,chrom,start,ref,alts,genotype,weight,state,conclusion,gene,category\n"
    "rs4244285,10,94781859,G,A,A/G,-0.8,risk,het,CYP2C19,cyp2c19\n"
    "rs1801133,1,11796321,G,A,A/G,0.4,protective,het,MTHFR,folate\n"
)
_STUDIES = (
    "rsid,pmid,population,p_value,conclusion,study_design\n"
    "rs4244285,29165669,T,0.05,E,U\nrs1801133,29165669,T,0.05,E,U\n"
)


def _publish(client: TestClient, key: str, version: str = "1.0.0") -> ModuleManifest:
    resp = client.post(
        "/api/v1/modules/just-dna-seq/coronary/versions",
        data={"version": version},
        files=[
            ("files", ("module_spec.yaml", _YAML.encode(), "text/yaml")),
            ("files", ("variants.csv", _VARIANTS.encode(), "text/csv")),
            ("files", ("studies.csv", _STUDIES.encode(), "text/csv")),
        ],
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 201, resp.text
    return ModuleManifest.model_validate(resp.json())


# ── Pure planner ─────────────────────────────────────────────────────────────────


def test_plan_backfills_from_state_and_is_idempotent() -> None:
    plan = plan_variants_upgrade(_VARIANTS)
    assert (plan.total_rows, plan.upgradable_rows) == (2, 2)

    rows = list(csv.DictReader(io.StringIO(plan.migrated_variants_csv)))
    by_rsid = {r["rsid"]: r for r in rows}
    # risk → direction risk, unknown significance; protective → protective.
    assert by_rsid["rs4244285"]["direction"] == "risk"
    assert by_rsid["rs4244285"]["stat_significance"] == "unknown"
    assert by_rsid["rs1801133"]["direction"] == "protective"
    # Original columns are untouched.
    assert by_rsid["rs4244285"]["gene"] == "CYP2C19"

    # Idempotent: re-planning the migrated CSV finds nothing left to do.
    assert plan_variants_upgrade(plan.migrated_variants_csv).upgradable_rows == 0


def test_plan_leaves_already_0_3_rows_alone() -> None:
    already = (
        "rsid,genotype,weight,state,conclusion,direction,stat_significance\n"
        "rs1,A/G,0.4,protective,ok,protective,significant\n"
    )
    assert plan_variants_upgrade(already).upgradable_rows == 0


# ── End-to-end through storage + republish ───────────────────────────────────────


def test_revalidate_reports_upgradable(client: TestClient, api_key: str, app) -> None:
    manifest = _publish(client, api_key)
    status, messages = revalidate_version(app.state.storage, "just-dna-seq", "coronary", "1.0.0", manifest)
    assert status == "upgradable", messages
    assert messages and "0.3 columns" in messages[0]


def test_upgrade_publishes_next_patch_and_leaves_predecessor(
    client: TestClient, api_key: str, app, settings: Settings
) -> None:
    manifest = _publish(client, api_key)
    result = upgrade_version(
        repo=app.state.repo, storage=app.state.storage, settings=settings,
        namespace="just-dna-seq", name="coronary", version="1.0.0", manifest=manifest,
    )
    assert result is not None
    new_version, new_manifest = result
    assert new_version == "1.0.1"

    # The successor's stored spec carries the back-populated 0.3 columns.
    migrated = app.state.storage.read_file(
        version_key("just-dna-seq", "coronary", "1.0.1"), "variants.csv"
    ).decode()
    by_rsid = {r["rsid"]: r for r in csv.DictReader(io.StringIO(migrated))}
    assert by_rsid["rs4244285"]["direction"] == "risk"

    # Predecessor is untouched (immutable) and now itself validates clean of drift.
    old = app.state.storage.read_file(
        version_key("just-dna-seq", "coronary", "1.0.0"), "variants.csv"
    ).decode()
    assert old == _VARIANTS

    # And re-publishing the successor would be a no-op: it no longer drifts.
    status, _ = revalidate_version(
        app.state.storage, "just-dna-seq", "coronary", "1.0.1", new_manifest
    )
    assert status == "ok"


def test_upgrade_is_noop_when_no_drift(client: TestClient, api_key: str, app, settings: Settings) -> None:
    manifest = _publish(client, api_key)
    # First upgrade produces 1.0.1; a second upgrade of 1.0.1 has nothing to do.
    first = upgrade_version(
        repo=app.state.repo, storage=app.state.storage, settings=settings,
        namespace="just-dna-seq", name="coronary", version="1.0.0", manifest=manifest,
    )
    assert first is not None
    _, upgraded_manifest = first
    second = upgrade_version(
        repo=app.state.repo, storage=app.state.storage, settings=settings,
        namespace="just-dna-seq", name="coronary", version="1.0.1", manifest=upgraded_manifest,
    )
    assert second is None


def test_superseded_predecessor_is_not_re_upgraded(
    client: TestClient, api_key: str, app, settings: Settings
) -> None:
    # The immutability bug: once 1.0.0 has been upgraded to 1.0.1, re-running upgrade on the still
    # drifted 1.0.0 must NOT mint 1.0.2 (and 1.0.3, …) forever — the successor masks it.
    manifest = _publish(client, api_key)
    repo, storage = app.state.repo, app.state.storage
    new_version, _ = upgrade_version(
        repo=repo, storage=storage, settings=settings,
        namespace="just-dna-seq", name="coronary", version="1.0.0", manifest=manifest,
    )
    assert new_version == "1.0.1"
    assert is_latest_version(repo, "just-dna-seq", "coronary", "1.0.1")
    assert not is_latest_version(repo, "just-dna-seq", "coronary", "1.0.0")

    # 1.0.0 still *drifts* on its own bytes (immutable) …
    assert plan_variants_upgrade(_VARIANTS).needed
    # … but upgrading it is now a no-op — no 1.0.2 is created.
    again = upgrade_version(
        repo=repo, storage=storage, settings=settings,
        namespace="just-dna-seq", name="coronary", version="1.0.0", manifest=manifest,
    )
    assert again is None
    assert not repo.version_exists("just-dna-seq", "coronary", "1.0.2")
