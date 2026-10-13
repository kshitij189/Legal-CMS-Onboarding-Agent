"""Proposal -> review -> approval through the API, with a deterministic fake LLM."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.agent.llm_client import LLMError
from app.api.mappings import get_llm_client
from app.api.schemas import get_adapter_factory
from app.integrations.base import TargetInspection
from app.main import app
from tests.conftest import SOURCE_FILES, read_fixture
from tests.integration.conftest import FirmContext, setup_firm
from tests.mapping_helpers import FakeLLM, ground_truth_proposals, target_schema


def _ready_project(client: TestClient, firm: str = "firm-c") -> FirmContext:
    """A project with the firm's source schema and the default target schema registered."""
    ctx = setup_firm(client, firm)
    fmt, path = SOURCE_FILES[firm]
    url = f"/api/v1/onboarding-projects/{ctx.project_id}"
    upload = client.post(
        f"{url}/source-schema",
        headers=ctx.specialist,
        json={"format": fmt, "content": read_fixture(path)},
    )
    assert upload.status_code == 201, upload.text

    class Adapter:
        def inspect_target(self) -> TargetInspection:
            return TargetInspection(normalized=target_schema(), config_revision=0)

    app.dependency_overrides[get_adapter_factory] = lambda: lambda connector: Adapter()
    assert client.post(f"{url}/inspect-target", headers=ctx.specialist).status_code == 201
    return ctx


def _use_llm(llm: FakeLLM) -> FakeLLM:
    app.dependency_overrides[get_llm_client] = lambda: llm
    return llm


def _url(ctx: FirmContext) -> str:
    return f"/api/v1/onboarding-projects/{ctx.project_id}"


def _propose(client: TestClient, ctx: FirmContext, **headers: str) -> Any:
    return client.post(f"{_url(ctx)}/propose-mappings", headers={**ctx.specialist, **headers})


def _review(
    client: TestClient, ctx: FirmContext, proposal_id: int, action: str, **body: Any
) -> Any:
    return client.post(
        f"{_url(ctx)}/mappings/{proposal_id}/review",
        headers=ctx.specialist,
        json={"action": action, **body},
    )


def _by_field(generation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["source_field"]: p for p in generation["proposals"] if p["review_status"] != "edited"}


def _review_all_for_firm_c(client: TestClient, ctx: FirmContext, gen: dict[str, Any]) -> None:
    """Approve everything sensible; resolve the ambiguous 'notes' by edit; reject 'urgent'."""
    for field, p in _by_field(gen).items():
        if field == "notes":
            edited = {
                **p["mapping"],
                "decision": "map",
                "target_field": "matter.summary",
                "transformation": "none",
                "candidates": [],
            }
            replacement = _review(
                client,
                ctx,
                p["id"],
                "edit",
                mapping=edited,
                comment="Free-form notes belong in the matter summary.",
            )
            assert replacement.status_code == 200, replacement.text
            approve = _review(
                client,
                ctx,
                replacement.json()["id"],
                "approve",
                comment="Instruction-like label reviewed; treated as data.",
            )
            assert approve.status_code == 200, approve.text
        elif field == "urgent":
            assert _review(client, ctx, p["id"], "reject").status_code == 200
        else:
            comment = "Reviewed." if p["validation_status"] == "needs_review" else None
            response = _review(client, ctx, p["id"], "approve", comment=comment)
            assert response.status_code == 200, (field, response.text)


def test_propose_review_approve_happy_path(client: TestClient, db: Session) -> None:
    ctx = _ready_project(client)
    llm = _use_llm(FakeLLM(ground_truth_proposals("firm-c")))
    proposed = _propose(client, ctx)
    assert proposed.status_code == 201, proposed.text
    gen = proposed.json()
    assert (gen["status"], gen["provider"], gen["prompt_version"]) == (
        "succeeded",
        "scripted",
        "mapping-v1",
    )
    assert gen["untrusted_source_fields"] == ["notes"]
    assert len(gen["proposals"]) == 10
    statuses = {f: p["validation_status"] for f, p in _by_field(gen).items()}
    assert statuses["pc_tel"] == "valid"
    assert statuses["notes"] == statuses["review_state"] == "needs_review"
    # The prompt carried the schemas as data only.
    assert "<untrusted_schema_data>" in llm.calls[0][1]

    early = client.post(
        f"{_url(ctx)}/approve-mappings", headers=ctx.specialist, json={"generation_id": gen["id"]}
    )
    assert early.status_code == 409
    assert early.json()["error"]["details"][0]["code"] == "unreviewed_proposals"

    _review_all_for_firm_c(client, ctx, gen)
    approved = client.post(
        f"{_url(ctx)}/approve-mappings", headers=ctx.specialist, json={"generation_id": gen["id"]}
    )
    assert approved.status_code == 201, approved.text
    version = approved.json()
    assert (version["version"], version["status"], version["approved_by"]) == (
        1,
        "approved",
        "firm-c.specialist",
    )
    planned = {i["mapping"]["source_field"] for i in version["items"]}
    assert planned == {
        "pc_name",
        "pc_tel",
        "pc_mail",
        "area_of_law",
        "other_side",
        "venue",
        "review_state",
        "call_window",
        "notes",
    }
    assert {e["source_field"] for e in version["excluded"]} == {"urgent"}
    assert version["complete"] is True and version["coverage_gaps"] == []

    again = client.post(
        f"{_url(ctx)}/approve-mappings", headers=ctx.specialist, json={"generation_id": gen["id"]}
    )
    assert again.status_code == 200 and again.json()["id"] == version["id"]

    actions = [
        e["action"] for e in client.get(f"{_url(ctx)}/audit-events", headers=ctx.specialist).json()
    ]
    assert actions[0] == "proposal.generated"
    assert actions.count("mapping.reviewed") == 11  # 10 proposals + approval of the edit
    assert actions[-1] == "mapping_version.approved"


def test_review_rules(client: TestClient) -> None:
    ctx = _ready_project(client)
    proposals = ground_truth_proposals("firm-c")
    proposals[0] = proposals[0].model_copy(update={"target_field": "contact.fax"})  # invalid
    _use_llm(FakeLLM(proposals))
    gen = _propose(client, ctx).json()
    by_field = _by_field(gen)

    invalid = _review(client, ctx, by_field["pc_name"]["id"], "approve")
    assert invalid.json()["error"]["details"][0]["code"] == "cannot_approve_invalid"
    ambiguous = _review(client, ctx, by_field["notes"]["id"], "approve", comment="ok")
    assert ambiguous.json()["error"]["details"][0]["code"] == "ambiguous_requires_edit"
    no_comment = _review(client, ctx, by_field["review_state"]["id"], "approve")
    assert no_comment.json()["error"]["details"][0]["code"] == "comment_required"
    clarify = _review(client, ctx, by_field["venue"]["id"], "request_clarification")
    assert clarify.status_code == 422

    fixed = {**by_field["pc_name"]["mapping"], "target_field": "contact.full_name"}
    replacement = _review(client, ctx, by_field["pc_name"]["id"], "edit", mapping=fixed)
    assert replacement.json()["validation_status"] == "valid"
    assert replacement.json()["supersedes_id"] == by_field["pc_name"]["id"]
    stale = _review(client, ctx, by_field["pc_name"]["id"], "approve")
    assert stale.status_code == 409 and stale.json()["error"]["details"][0]["code"] == "superseded"
    other_field = {**fixed, "source_field": "pc_tel"}
    moved = _review(client, ctx, replacement.json()["id"], "edit", mapping=other_field)
    assert moved.json()["error"]["details"][0]["code"] == "source_field_changed"


def test_clarification_blocks_approval(client: TestClient) -> None:
    ctx = _ready_project(client)
    _use_llm(FakeLLM(ground_truth_proposals("firm-c")))
    gen = _propose(client, ctx).json()
    _review_all_for_firm_c(client, ctx, gen)
    venue = _by_field(gen)["venue"]["id"]
    _review(client, ctx, venue, "request_clarification", comment="Is venue the jurisdiction?")
    blocked = client.post(
        f"{_url(ctx)}/approve-mappings", headers=ctx.specialist, json={"generation_id": gen["id"]}
    )
    assert blocked.status_code == 409


def test_schema_change_after_proposal_requires_new_proposals(client: TestClient) -> None:
    ctx = _ready_project(client)
    _use_llm(FakeLLM(ground_truth_proposals("firm-c")))
    gen = _propose(client, ctx).json()
    _review_all_for_firm_c(client, ctx, gen)
    changed = read_fixture(SOURCE_FILES["firm-c"][1]).replace(
        'name="venue" type="text"', 'name="venue" type="text" required'
    )
    upload = client.post(
        f"{_url(ctx)}/source-schema",
        headers=ctx.specialist,
        json={"format": "html_form", "content": changed},
    )
    assert upload.json()["schema_version"]["version"] == 2
    response = client.post(
        f"{_url(ctx)}/approve-mappings", headers=ctx.specialist, json={"generation_id": gen["id"]}
    )
    assert response.status_code == 409
    assert response.json()["error"]["details"][0]["code"] == "schema_changed"


def test_duplicate_targets_cannot_be_approved_together(client: TestClient) -> None:
    ctx = _ready_project(client)
    proposals = ground_truth_proposals("firm-c")
    by_key = {p.source_field: i for i, p in enumerate(proposals)}
    proposals[by_key["venue"]] = proposals[by_key["venue"]].model_copy(
        update={"target_field": "matter.opposing_party"}
    )
    _use_llm(FakeLLM(proposals))
    gen = _propose(client, ctx).json()
    assert "duplicate_target" in [i["code"] for i in _by_field(gen)["venue"]["validation_issues"]]
    _review_all_for_firm_c(client, ctx, gen)
    response = client.post(
        f"{_url(ctx)}/approve-mappings", headers=ctx.specialist, json={"generation_id": gen["id"]}
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["code"] == "invalid_plan"


def test_skipped_fields_get_a_placeholder_for_human_decision(client: TestClient) -> None:
    ctx = _ready_project(client)
    _use_llm(FakeLLM([p for p in ground_truth_proposals("firm-c") if p.source_field != "urgent"]))
    gen = _propose(client, ctx).json()
    assert gen["unproposed_source_fields"] == ["urgent"]
    placeholder = _by_field(gen)["urgent"]
    assert placeholder["origin"] == "system"
    assert placeholder["validation_issues"][0]["code"] == "not_proposed"


def test_idempotency_key_prevents_a_second_llm_call(client: TestClient) -> None:
    ctx = _ready_project(client)
    llm = _use_llm(FakeLLM(ground_truth_proposals("firm-c")))
    first = _propose(client, ctx, **{"Idempotency-Key": "run-1"})
    second = _propose(client, ctx, **{"Idempotency-Key": "run-1"})
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert len(llm.calls) == 1


def test_llm_failure_is_recorded_and_reported(client: TestClient, db: Session) -> None:
    ctx = _ready_project(client)
    _use_llm(FakeLLM(error=LLMError("quota", "Quota exhausted.", "gemini")))
    response = _propose(client, ctx)
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "llm_error"
    assert (error["details"][0]["category"], error["details"][0]["provider"]) == ("quota", "gemini")
    assert db.execute(text("SELECT count(*) FROM mapping_proposals")).scalar_one() == 0
    row = db.execute(text("SELECT status, error_category FROM proposal_generations")).one()
    assert tuple(row) == ("failed", "quota")
    events = client.get(f"{_url(ctx)}/audit-events", headers=ctx.specialist).json()
    assert [e["action"] for e in events] == ["proposal.generation_failed"]
    assert client.get(f"{_url(ctx)}/mappings", headers=ctx.specialist).status_code == 404


def test_proposals_require_schemas(client: TestClient) -> None:
    ctx = setup_firm(client)
    _use_llm(FakeLLM(ground_truth_proposals("firm-a")))
    response = _propose(client, ctx)
    assert response.status_code == 409
    assert response.json()["error"]["details"][0]["code"] == "schemas_missing"


def test_database_enforces_immutability(client: TestClient, db: Session) -> None:
    ctx = _ready_project(client)
    _use_llm(FakeLLM(ground_truth_proposals("firm-c")))
    gen = _propose(client, ctx).json()
    _review_all_for_firm_c(client, ctx, gen)
    client.post(
        f"{_url(ctx)}/approve-mappings", headers=ctx.specialist, json={"generation_id": gen["id"]}
    )
    for statement in (
        "UPDATE mapping_versions SET items = '[]'::jsonb",
        "UPDATE mapping_proposals SET content = '{}'::jsonb",
        "UPDATE audit_events SET action = 'tampered'",
    ):
        with pytest.raises(DBAPIError):
            db.execute(text(statement))
        db.rollback()
    # Status changes stay possible (e.g. superseding a version).
    db.execute(text("UPDATE mapping_versions SET status = 'superseded'"))
    db.rollback()


def test_new_approval_supersedes_the_previous_version(client: TestClient) -> None:
    ctx = _ready_project(client)
    _use_llm(FakeLLM(ground_truth_proposals("firm-c")))
    first = _propose(client, ctx).json()
    _review_all_for_firm_c(client, ctx, first)
    v1 = client.post(
        f"{_url(ctx)}/approve-mappings", headers=ctx.specialist, json={"generation_id": first["id"]}
    ).json()
    second = _propose(client, ctx).json()
    _review_all_for_firm_c(client, ctx, second)
    v2 = client.post(
        f"{_url(ctx)}/approve-mappings",
        headers=ctx.specialist,
        json={"generation_id": second["id"]},
    ).json()
    assert v2["version"] == 2
    versions = client.get(f"{_url(ctx)}/mapping-versions", headers=ctx.specialist).json()
    assert [(v["id"], v["status"]) for v in versions] == [
        (v1["id"], "superseded"),
        (v2["id"], "approved"),
    ]


def test_other_firms_cannot_see_or_review(client: TestClient) -> None:
    ctx = _ready_project(client, "firm-c")
    other = setup_firm(client, "firm-b")
    _use_llm(FakeLLM(ground_truth_proposals("firm-c")))
    gen = _propose(client, ctx).json()
    url = _url(ctx)
    proposal_id = gen["proposals"][0]["id"]
    for method, path, body in (
        ("POST", f"{url}/propose-mappings", None),
        ("GET", f"{url}/mappings", None),
        ("POST", f"{url}/mappings/{proposal_id}/review", {"action": "reject"}),
        ("POST", f"{url}/approve-mappings", {"generation_id": gen["id"]}),
        ("GET", f"{url}/mapping-versions", None),
        ("GET", f"{url}/audit-events", None),
    ):
        response = client.request(method, path, headers=other.specialist, json=body)
        assert response.status_code == 404, (method, path)
    # A proposal id from another project is not reachable through this firm's own project.
    own = client.post(
        f"{_url(other)}/mappings/{proposal_id}/review",
        headers=other.specialist,
        json={"action": "reject"},
    )
    assert own.status_code == 404


def test_writes_are_only_reachable_through_an_approved_version() -> None:
    """The only write endpoint takes an approved mapping version id; the LLM has no write tools."""
    paths = app.openapi()["paths"]
    writing = [p for p in paths if any(w in p for w in ("execute", "apply", "configure"))]
    assert writing == ["/api/v1/onboarding-projects/{project_id}/execute"]
    schema = app.openapi()["components"]["schemas"]["ExecuteRequest"]
    assert set(schema["properties"]) == {"mapping_version_id", "channel"}
