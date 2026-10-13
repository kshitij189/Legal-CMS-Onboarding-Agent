"""Execution of approved versions through the API, against an in-memory CMS with fault injection."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.execution import get_reader_factory, get_writer_factory
from app.main import app
from tests.conftest import SOURCE_FILES, read_fixture
from tests.execution_helpers import FakeCMS
from tests.integration.conftest import FirmContext, setup_firm
from tests.integration.test_mappings_api import _propose, _ready_project, _url, _use_llm
from tests.mapping_helpers import FakeLLM, ground_truth_proposals


def approve_all(client: TestClient, ctx: FirmContext, gen: dict[str, Any]) -> dict[str, Any]:
    """Approve every proposal a reviewer could approve; reject ambiguous/invalid ones."""
    for p in gen["proposals"]:
        action = (
            "reject"
            if p["decision"] == "ambiguous" or p["validation_status"] == "invalid"
            else "approve"
        )
        comment = "Reviewed." if p["validation_status"] == "needs_review" else None
        response = client.post(
            f"{_url(ctx)}/mappings/{p['id']}/review",
            headers=ctx.specialist,
            json={"action": action, "comment": comment},
        )
        assert response.status_code == 200, response.text
    version = client.post(
        f"{_url(ctx)}/approve-mappings", headers=ctx.specialist, json={"generation_id": gen["id"]}
    )
    assert version.status_code == 201, version.text
    return version.json()  # type: ignore[no-any-return]


def approved_project(client: TestClient, firm: str) -> tuple[FirmContext, dict[str, Any]]:
    ctx = _ready_project(client, firm)
    _use_llm(FakeLLM(ground_truth_proposals(firm)))
    return ctx, approve_all(client, ctx, _propose(client, ctx).json())


def use_cms(cms: FakeCMS) -> FakeCMS:
    app.dependency_overrides[get_reader_factory] = lambda: cms.reader_factory
    app.dependency_overrides[get_writer_factory] = lambda: cms.writer_factory
    return cms


def execute(client: TestClient, ctx: FirmContext, version_id: int, key: str | None = None) -> Any:
    headers = {**ctx.specialist, **({"Idempotency-Key": key} if key else {})}
    return client.post(
        f"{_url(ctx)}/execute",
        headers=headers,
        json={"mapping_version_id": version_id, "channel": "api"},
    )


def test_execute_applies_and_verifies(client: TestClient) -> None:
    ctx, version = approved_project(client, "firm-a")
    cms = use_cms(FakeCMS())
    response = execute(client, ctx, version["id"])
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "succeeded", run
    assert run["summary"]["steps"] == {"applied": 8}
    assert run["summary"]["verification"] == {"match": 8}
    assert (run["config_revision_before"], run["config_revision_after"]) == (0, 8)
    assert len(cms.writes) == 8
    report = client.get(f"{_url(ctx)}/verification", headers=ctx.specialist).json()
    assert report["id"] == run["id"]
    actions = [
        e["action"] for e in client.get(f"{_url(ctx)}/audit-events", headers=ctx.specialist).json()
    ]
    assert actions[-2:] == ["run.started", "run.succeeded"]


def test_rerun_changes_nothing(client: TestClient) -> None:
    ctx, version = approved_project(client, "firm-a")
    cms = use_cms(FakeCMS())
    execute(client, ctx, version["id"])
    writes = len(cms.writes)
    second = execute(client, ctx, version["id"]).json()
    assert second["status"] == "succeeded"
    assert second["summary"]["steps"] == {"already_applied": 8}
    assert len(cms.writes) == writes
    assert second["config_revision_before"] == second["config_revision_after"] == 8


def test_idempotency_key_returns_the_same_run(client: TestClient) -> None:
    ctx, version = approved_project(client, "firm-a")
    cms = use_cms(FakeCMS())
    first = execute(client, ctx, version["id"], key="exec-1")
    again = execute(client, ctx, version["id"], key="exec-1")
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["id"] == again.json()["id"]
    assert len(cms.writes) == 8


def test_lost_response_after_success_is_resolved_by_read_back(client: TestClient) -> None:
    ctx, version = approved_project(client, "firm-a")
    cms = use_cms(FakeCMS({"client_email": ["uncertain_applied"]}))
    run = execute(client, ctx, version["id"]).json()
    step = next(s for s in run["steps"] if s["subject"] == "client_email")
    assert (step["status"], step["attempts"]) == ("applied", 1)
    assert step["evidence"]["resolved_by_read_back"] is True
    assert cms.writes.count("client_email") == 1  # never written twice
    assert run["status"] == "succeeded"


def test_lost_write_is_retried_once_after_checking(client: TestClient) -> None:
    ctx, version = approved_project(client, "firm-a")
    cms = use_cms(FakeCMS({"client_email": ["uncertain_lost"]}))
    run = execute(client, ctx, version["id"]).json()
    step = next(s for s in run["steps"] if s["subject"] == "client_email")
    assert (step["status"], step["attempts"]) == ("applied", 2)
    assert cms.writes.count("client_email") == 2
    assert run["status"] == "succeeded"


def test_persistent_uncertainty_fails_without_a_third_attempt(client: TestClient) -> None:
    ctx, version = approved_project(client, "firm-a")
    cms = use_cms(FakeCMS({"client_email": ["uncertain_lost", "uncertain_lost", "uncertain_lost"]}))
    run = execute(client, ctx, version["id"]).json()
    step = next(s for s in run["steps"] if s["subject"] == "client_email")
    assert (step["status"], step["attempts"], step["error_category"]) == ("failed", 2, "uncertain")
    assert cms.writes.count("client_email") == 2
    assert run["status"] == "failed"


def test_ui_drift_stops_the_run_and_skips_the_rest(client: TestClient) -> None:
    ctx, version = approved_project(client, "firm-a")
    use_cms(FakeCMS({"client_mobile": ["ui_drift"]}))
    run = execute(client, ctx, version["id"]).json()
    statuses = {s["subject"]: s["status"] for s in run["steps"]}
    assert statuses["client_email"] == "applied"  # sequence: callback_time, client_email, ...
    assert statuses["client_mobile"] == "failed"
    assert {statuses[k] for k in ("jurisdiction", "matter_type", "opposing_party")} == {"skipped"}
    assert run["status"] == "failed" and run["error_category"] == "ui_drift"
    # The report shows the real state: what landed and what did not.
    checks = {c["subject"]: c["status"] for c in run["verification"]}
    assert checks["client_email"] == "match" and checks["client_mobile"] == "missing"


def test_validation_rejection_is_not_retried(client: TestClient) -> None:
    ctx, version = approved_project(client, "firm-a")
    cms = use_cms(FakeCMS({"matter_type": ["validation"]}))
    run = execute(client, ctx, version["id"]).json()
    step = next(s for s in run["steps"] if s["subject"] == "matter_type")
    assert (step["status"], step["attempts"], step["error_category"]) == ("failed", 1, "validation")
    assert cms.writes.count("matter_type") == 1


def test_target_drift_blocks_before_any_write(client: TestClient) -> None:
    ctx, version = approved_project(client, "firm-a")
    cms = FakeCMS()
    phone = cms.config.field("contact.phone_number")
    assert phone is not None
    phone.data_type = "string"
    use_cms(cms)
    run = execute(client, ctx, version["id"]).json()
    assert run["status"] == "blocked" and run["error_category"] == "target_drift"
    assert run["summary"]["steps"] == {"skipped": 8}
    assert cms.writes == []
    assert any(d["field"] == "contact.phone_number" and d["blocking"] for d in run["drift_issues"])


def test_custom_fields_are_created_before_mappings_that_use_them(client: TestClient) -> None:
    ctx, version = approved_project(client, "firm-c")
    cms = use_cms(FakeCMS())
    run = execute(client, ctx, version["id"]).json()
    assert run["status"] == "succeeded", run
    assert cms.writes[0] == "matter.custom.review_status"
    assert cms.writes.index("review_state") > 0
    field = cms.config.field("matter.custom.review_status")
    assert field is not None and field.required is True


def test_source_schema_change_after_approval_blocks_execution(client: TestClient) -> None:
    ctx, version = approved_project(client, "firm-a")
    cms = use_cms(FakeCMS())
    changed = read_fixture(SOURCE_FILES["firm-a"][1]).replace(
        '"required": ["client_full_name"', '"required": ["client_email", "client_full_name"'
    )
    client.post(
        f"{_url(ctx)}/source-schema",
        headers=ctx.specialist,
        json={"format": "json_schema", "content": changed},
    )
    response = execute(client, ctx, version["id"])
    assert response.status_code == 409
    assert response.json()["error"]["details"][0]["code"] == "schema_changed"
    assert cms.writes == []


def test_superseded_version_cannot_run(client: TestClient) -> None:
    ctx, v1 = approved_project(client, "firm-a")
    approve_all(client, ctx, _propose(client, ctx).json())
    use_cms(FakeCMS())
    response = execute(client, ctx, v1["id"])
    assert response.status_code == 409
    assert response.json()["error"]["details"][0]["code"] == "version_not_current"


def test_only_one_active_run_per_project(client: TestClient, db: Session) -> None:
    ctx, version = approved_project(client, "firm-a")
    use_cms(FakeCMS())
    db.execute(
        text(
            "INSERT INTO onboarding_runs (firm_id, project_id, mapping_version_id, channel, "
            "status, drift_issues, created_by_id, started_at) "
            "SELECT p.firm_id, p.id, :v, 'api', 'running', '[]', p.created_by_id, now() "
            "FROM onboarding_projects p WHERE p.id = :p"
        ),
        {"v": version["id"], "p": ctx.project_id},
    )
    db.commit()
    response = execute(client, ctx, version["id"])
    assert response.status_code == 409
    assert response.json()["error"]["details"][0]["code"] == "run_in_progress"


def test_connector_must_be_authorized_for_the_channel(client: TestClient, db: Session) -> None:
    ctx, version = approved_project(client, "firm-a")
    use_cms(FakeCMS())
    db.execute(text("UPDATE cms_connectors SET supported_operations = '[\"inspect_target\"]'"))
    db.commit()
    response = execute(client, ctx, version["id"])
    assert response.status_code == 403
    assert response.json()["error"]["details"][0]["code"] == "operation_not_permitted"


def test_execute_accepts_only_an_approved_version_id(client: TestClient) -> None:
    ctx, version = approved_project(client, "firm-a")
    use_cms(FakeCMS())
    arbitrary = client.post(
        f"{_url(ctx)}/execute",
        headers=ctx.specialist,
        json={
            "mapping_version_id": version["id"],
            "actions": [{"upsert_mapping": {"source_key": "x"}}],
        },
    )
    assert arbitrary.status_code == 422
    unknown = execute(client, ctx, 99999)
    assert unknown.status_code == 404


def test_other_firms_cannot_execute_or_read_runs(client: TestClient) -> None:
    ctx, version = approved_project(client, "firm-a")
    other = setup_firm(client, "firm-b")
    cms = use_cms(FakeCMS())
    run = execute(client, ctx, version["id"]).json()
    url = _url(ctx)
    assert execute(client, other, version["id"]).status_code == 404  # other's own project
    for path in (f"{url}/runs", f"{url}/runs/{run['id']}", f"{url}/verification"):
        assert client.get(path, headers=other.specialist).status_code == 404
    forbidden = client.post(
        f"{url}/execute",
        headers=other.specialist,
        json={"mapping_version_id": version["id"], "channel": "api"},
    )
    assert forbidden.status_code == 404
    assert len(cms.writes) == 8


@pytest.mark.parametrize("channel", ["api", "browser"])
def test_channel_is_recorded(client: TestClient, channel: str) -> None:
    ctx, version = approved_project(client, "firm-a")
    use_cms(FakeCMS())
    run = client.post(
        f"{_url(ctx)}/execute",
        headers=ctx.specialist,
        json={"mapping_version_id": version["id"], "channel": channel},
    ).json()
    assert run["channel"] == channel and run["status"] == "succeeded"
