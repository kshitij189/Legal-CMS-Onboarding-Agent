"""Execution-safety scenarios (blueprint §13): the real API, database and execution stages against
the in-memory CMS with injected faults. Each scenario returns pass/fail plus observations, and
tags that feed the metrics:

- expect_success:  the run must succeed and verify          -> verification success rate
- no_write:        the CMS must not be written at all        -> unsafe-write rate
- duplicate:       a repeat must not write again             -> duplicate-execution rate
- recovery:        an interrupted/uncertain write resolves   -> recovery success rate
Every run that reports "succeeded" is also re-checked against the CMS (false-success count).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.domain import execution_service
from app.domain.execution_plan import verify
from app.evaluation.fixtures import SOURCE_FILES, ground_truth_proposals, read
from app.evaluation.simulation import InMemoryCMS, ScriptedLLM, SimulatedCrash, target_snapshot
from app.integrations.base import TargetInspection
from app.schemas.execution import PlannedAction

PASSWORD = "evaluation-password"  # noqa: S105 - synthetic accounts in a throwaway DB


@dataclass
class Context:
    client: TestClient
    slug: str
    admin: dict[str, str] = field(default_factory=dict)
    specialist: dict[str, str] = field(default_factory=dict)
    project_id: int = 0
    connector_id: int = 0

    @property
    def url(self) -> str:
        return f"/api/v1/onboarding-projects/{self.project_id}"


@dataclass
class Outcome:
    passed: bool
    observations: dict[str, Any]
    runs: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ExecutionScenario:
    id: str
    category: str
    description: str
    tags: tuple[str, ...]
    run: Callable[[TestClient, str], Outcome]


# --- helpers ------------------------------------------------------------------------------------


def _login(client: TestClient, username: str) -> dict[str, str]:
    r = client.post("/api/v1/auth/token", json={"username": username, "password": PASSWORD})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _project(client: TestClient, slug: str, firm: str) -> Context:
    from app.api.schemas import get_adapter_factory
    from app.main import app

    ctx = Context(client=client, slug=slug)
    client.post(
        "/api/v1/firms",
        json={
            "firm": {"slug": slug, "name": slug},
            "admin": {"username": f"{slug}.admin", "display_name": "Admin", "password": PASSWORD},
        },
    ).raise_for_status()
    ctx.admin = _login(client, f"{slug}.admin")
    client.post(
        "/api/v1/users",
        headers=ctx.admin,
        json={
            "username": f"{slug}.specialist",
            "display_name": "Specialist",
            "password": PASSWORD,
            "role": "integration_specialist",
        },
    ).raise_for_status()
    ctx.specialist = _login(client, f"{slug}.specialist")
    connector = client.post(
        "/api/v1/connectors",
        headers=ctx.admin,
        json={
            "name": "Mock CMS",
            "connector_type": "mock_cms_api",
            "base_url": "http://localhost:8100",
            "credential_ref": f"{slug}-cms",
        },
    )
    connector.raise_for_status()
    ctx.connector_id = connector.json()["id"]
    project = client.post(
        "/api/v1/onboarding-projects",
        headers=ctx.admin,
        json={"connector_id": ctx.connector_id, "name": slug},
    )
    project.raise_for_status()
    ctx.project_id = project.json()["id"]
    fmt, path = SOURCE_FILES[firm]
    client.post(
        f"{ctx.url}/source-schema",
        headers=ctx.specialist,
        json={"format": fmt, "content": read(path)},
    ).raise_for_status()

    class Adapter:
        def inspect_target(self) -> TargetInspection:
            return TargetInspection(normalized=target_snapshot(), config_revision=0)

    app.dependency_overrides[get_adapter_factory] = lambda: lambda connector: Adapter()
    client.post(f"{ctx.url}/inspect-target", headers=ctx.specialist).raise_for_status()
    return ctx


def _approve(ctx: Context, firm: str, reject: tuple[str, ...] = ()) -> dict[str, Any]:
    from app.api.mappings import get_llm_client
    from app.main import app

    app.dependency_overrides[get_llm_client] = lambda: ScriptedLLM(ground_truth_proposals(firm))
    generation = ctx.client.post(f"{ctx.url}/propose-mappings", headers=ctx.specialist)
    generation.raise_for_status()
    for p in generation.json()["proposals"]:
        action = (
            "reject"
            if p["decision"] == "ambiguous"
            or p["validation_status"] == "invalid"
            or p["source_field"] in reject
            else "approve"
        )
        comment = "Reviewed." if p["validation_status"] == "needs_review" else None
        ctx.client.post(
            f"{ctx.url}/mappings/{p['id']}/review",
            headers=ctx.specialist,
            json={"action": action, "comment": comment},
        ).raise_for_status()
    version = ctx.client.post(
        f"{ctx.url}/approve-mappings",
        headers=ctx.specialist,
        json={"generation_id": generation.json()["id"]},
    )
    version.raise_for_status()
    result: dict[str, Any] = version.json()
    return result


_current_cms: list[InMemoryCMS] = []


def _use(cms: InMemoryCMS) -> None:
    from app.api.execution import get_reader_factory, get_writer_factory
    from app.main import app

    _current_cms[:] = [cms]  # the runner re-checks "succeeded" runs against this CMS

    app.dependency_overrides[get_reader_factory] = lambda: cms.reader_factory
    app.dependency_overrides[get_writer_factory] = lambda: cms.writer_factory


def _execute(ctx: Context, version_id: int, key: str | None = None) -> Any:
    headers = {**ctx.specialist, **({"Idempotency-Key": key} if key else {})}
    return ctx.client.post(
        f"{ctx.url}/execute",
        headers=headers,
        json={"mapping_version_id": version_id, "channel": "api"},
    )


def _setup(
    client: TestClient,
    slug: str,
    firm: str = "firm-a",
    cms: InMemoryCMS | None = None,
    reject: tuple[str, ...] = (),
) -> tuple[Context, dict[str, Any], InMemoryCMS]:
    ctx = _project(client, slug, firm)
    version = _approve(ctx, firm, reject)
    cms = cms or InMemoryCMS()
    _use(cms)
    return ctx, version, cms


# --- scenarios ----------------------------------------------------------------------------------


def happy_path(client: TestClient, slug: str) -> Outcome:
    ctx, version, cms = _setup(client, slug)
    run = _execute(ctx, version["id"]).json()
    ok = run["status"] == "succeeded" and run["summary"]["verification"] == {"match": 8}
    return Outcome(
        ok, {"status": run["status"], "summary": run["summary"], "writes": len(cms.writes)}, [run]
    )


def custom_field_first(client: TestClient, slug: str) -> Outcome:
    ctx, version, cms = _setup(client, slug, "firm-c")
    run = _execute(ctx, version["id"]).json()
    ok = run["status"] == "succeeded" and cms.writes[:1] == ["matter.custom.review_status"]
    return Outcome(ok, {"status": run["status"], "write_order": cms.writes}, [run])


def repeated_execution(client: TestClient, slug: str) -> Outcome:
    ctx, version, cms = _setup(client, slug)
    first = _execute(ctx, version["id"]).json()
    writes, revision = len(cms.writes), cms.config.config_revision
    second = _execute(ctx, version["id"]).json()
    ok = (
        second["status"] == "succeeded"
        and len(cms.writes) == writes
        and cms.config.config_revision == revision
        and second["summary"]["steps"] == {"already_applied": 8}
    )
    return Outcome(
        ok,
        {"extra_writes": len(cms.writes) - writes, "second_run_steps": second["summary"]["steps"]},
        [first, second],
    )


def same_idempotency_key(client: TestClient, slug: str) -> Outcome:
    ctx, version, cms = _setup(client, slug)
    first = _execute(ctx, version["id"], key="once")
    second = _execute(ctx, version["id"], key="once")
    ok = (
        second.status_code == 200
        and first.json()["id"] == second.json()["id"]
        and len(cms.writes) == 8
    )
    return Outcome(
        ok,
        {"same_run": first.json()["id"] == second.json()["id"], "writes": len(cms.writes)},
        [first.json()],
    )


def timeout_after_success(client: TestClient, slug: str) -> Outcome:
    ctx, version, cms = _setup(
        client, slug, cms=InMemoryCMS({"client_email": ["uncertain_applied"]})
    )
    run = _execute(ctx, version["id"]).json()
    step = next(s for s in run["steps"] if s["subject"] == "client_email")
    ok = (
        run["status"] == "succeeded"
        and cms.writes.count("client_email") == 1
        and step["evidence"].get("resolved_by_read_back") is True
    )
    return Outcome(
        ok,
        {"writes_of_step": cms.writes.count("client_email"), "evidence": step["evidence"]},
        [run],
    )


def lost_write_retried_once(client: TestClient, slug: str) -> Outcome:
    ctx, version, cms = _setup(client, slug, cms=InMemoryCMS({"client_email": ["uncertain_lost"]}))
    run = _execute(ctx, version["id"]).json()
    step = next(s for s in run["steps"] if s["subject"] == "client_email")
    ok = run["status"] == "succeeded" and step["attempts"] == 2
    return Outcome(ok, {"attempts": step["attempts"]}, [run])


def ui_control_renamed(client: TestClient, slug: str) -> Outcome:
    ctx, version, cms = _setup(client, slug, cms=InMemoryCMS({"client_full_name": ["ui_drift"]}))
    run = _execute(ctx, version["id"]).json()
    statuses = [s["status"] for s in run["steps"]]
    failed_at = statuses.index("failed") if "failed" in statuses else -1
    ok = (
        run["status"] == "failed"
        and run["error_category"] == "ui_drift"
        and failed_at >= 0
        and set(statuses[failed_at + 1 :]) <= {"skipped"}
        and "client_full_name" not in [m.source_key for m in cms.config.mappings]
    )
    return Outcome(
        ok,
        {
            "status": run["status"],
            "steps": statuses,
            "writes_after_failure": len(cms.writes) - (failed_at + 1),
        },
        [run],
    )


def crash_after_write(client: TestClient, slug: str) -> Outcome:
    """The process dies right after a write; a fresh executor resumes the run."""
    from app.api.execution import get_run_dispatcher
    from app.main import app

    cms = InMemoryCMS({"matter.custom.review_status": ["crash_after_apply"]})
    ctx, version, _ = _setup(client, slug, "firm-c", cms=cms)
    app.dependency_overrides[get_run_dispatcher] = lambda: lambda db, run: None
    run_id = _execute(ctx, version["id"]).json()["id"]
    del app.dependency_overrides[get_run_dispatcher]
    with get_sessionmaker()() as db:
        assert execution_service.preflight(db, run_id, cms.reader_factory)  # noqa: S101
        try:
            execution_service.execute_step(db, run_id, 1, cms.reader_factory, cms.writer_factory)
        except SimulatedCrash:
            db.rollback()
    with get_sessionmaker()() as db:  # "new worker"
        execution_service.run_inline(db, run_id, cms.reader_factory, cms.writer_factory)
    run = ctx.client.get(f"{ctx.url}/runs/{run_id}", headers=ctx.specialist).json()
    first = run["steps"][0]
    ok = (
        run["status"] == "succeeded"
        and cms.writes.count("matter.custom.review_status") == 1
        and first["evidence"].get("after_interruption") is True
    )
    return Outcome(
        ok,
        {
            "field_writes": cms.writes.count("matter.custom.review_status"),
            "first_step": {k: first[k] for k in ("status", "attempts", "evidence")},
        },
        [run],
    )


def connector_not_authorized(client: TestClient, slug: str) -> Outcome:
    ctx, version, cms = _setup(client, slug)
    with get_sessionmaker()() as db:
        db.execute(
            text(
                "UPDATE cms_connectors SET supported_operations = '[\"inspect_target\"]'"
                " WHERE id = :c"
            ),
            {"c": ctx.connector_id},
        )
        db.commit()
    response = _execute(ctx, version["id"])
    ok = response.status_code == 403 and cms.writes == []
    return Outcome(ok, {"http_status": response.status_code, "writes": len(cms.writes)})


def superseded_version(client: TestClient, slug: str) -> Outcome:
    ctx, v1, cms = _setup(client, slug)
    _approve(ctx, "firm-a")  # a newer approval supersedes v1
    response = _execute(ctx, v1["id"])
    ok = response.status_code == 409 and cms.writes == []
    return Outcome(
        ok,
        {
            "http_status": response.status_code,
            "error": response.json()["error"]["details"][0]["code"],
            "writes": len(cms.writes),
        },
    )


def schema_changed_after_approval(client: TestClient, slug: str) -> Outcome:
    ctx, version, cms = _setup(client, slug)
    changed = read(SOURCE_FILES["firm-a"][1]).replace(
        '"required": ["client_full_name"', '"required": ["client_email", "client_full_name"'
    )
    ctx.client.post(
        f"{ctx.url}/source-schema",
        headers=ctx.specialist,
        json={"format": "json_schema", "content": changed},
    ).raise_for_status()
    response = _execute(ctx, version["id"])
    ok = response.status_code == 409 and cms.writes == []
    return Outcome(
        ok,
        {
            "http_status": response.status_code,
            "error": response.json()["error"]["details"][0]["code"],
            "writes": len(cms.writes),
        },
    )


def target_drift(client: TestClient, slug: str) -> Outcome:
    cms = InMemoryCMS()
    phone = cms.config.field("contact.phone_number")
    assert phone is not None  # noqa: S101
    phone.data_type = "string"
    ctx, version, _ = _setup(client, slug, cms=cms)
    run = _execute(ctx, version["id"]).json()
    ok = run["status"] == "blocked" and cms.writes == []
    return Outcome(
        ok,
        {
            "status": run["status"],
            "writes": len(cms.writes),
            "blocking": [d for d in run["drift_issues"] if d["blocking"]],
        },
        [run],
    )


def unexpected_cms_field(client: TestClient, slug: str) -> Outcome:
    cms = InMemoryCMS()
    extra = cms.config.fields[0].model_copy(
        update={"key": "contact.custom.loyalty_tier", "label": "Loyalty tier", "is_custom": True}
    )
    cms.config.fields.append(extra)
    ctx, version, _ = _setup(client, slug, cms=cms)
    run = _execute(ctx, version["id"]).json()
    reported = any(
        d["field"] == "contact.custom.loyalty_tier" and not d["blocking"]
        for d in run["drift_issues"]
    )
    untouched = cms.config.field("contact.custom.loyalty_tier") == extra
    ok = run["status"] == "succeeded" and reported and untouched
    return Outcome(
        ok,
        {
            "status": run["status"],
            "reported_non_blocking": reported,
            "preserved_untouched": untouched,
        },
        [run],
    )


def silent_wrong_save(client: TestClient, slug: str) -> Outcome:
    ctx, version, cms = _setup(
        client, slug, cms=InMemoryCMS({"client_mobile": ["store_different"]})
    )
    run = _execute(ctx, version["id"]).json()
    check = next(c for c in run["verification"] if c["subject"] == "client_mobile")
    ok = run["status"] == "verification_failed" and check["status"] == "mismatch"
    return Outcome(ok, {"status": run["status"], "check": check["reason"]}, [run])


def unmanaged_mapping_conflict(client: TestClient, slug: str) -> Outcome:
    cms = InMemoryCMS()
    from app.schemas.execution import MappingSpec

    legacy = MappingSpec(
        source_key="legacy_email", target_field="contact.email", transformation="none"
    )
    cms.config.mappings.append(legacy)
    ctx, version, _ = _setup(client, slug, cms=cms)
    run = _execute(ctx, version["id"]).json()
    ok = (
        run["status"] == "failed"
        and run["error_category"] == "conflict"
        and legacy in cms.config.mappings
    )
    return Outcome(
        ok, {"status": run["status"], "legacy_preserved": legacy in cms.config.mappings}, [run]
    )


def missing_required_source(client: TestClient, slug: str) -> Outcome:
    """Firm B: the matter category is rejected in review, so the CMS-required field gets no
    source. The approved version must be marked incomplete with the gap named."""
    ctx, version, cms = _setup(client, slug, "firm-b", reject=("case_category",))
    gaps = [g["target_field"] for g in version["coverage_gaps"]]
    run = _execute(ctx, version["id"]).json()
    ok = (
        version["complete"] is False
        and gaps == ["matter.practice_area"]
        and run["status"] == "succeeded"
    )
    return Outcome(
        ok,
        {
            "version_complete": version["complete"],
            "coverage_gaps": gaps,
            "run_status": run["status"],
        },
        [run],
    )


SCENARIOS = [
    ExecutionScenario(
        "exe-01-happy-path",
        "clear_one_to_one",
        "Approved Firm A plan applied and verified.",
        ("expect_success",),
        happy_path,
    ),
    ExecutionScenario(
        "exe-02-custom-field-first",
        "clear_one_to_one",
        "Firm C: custom field created before the mapping that uses it.",
        ("expect_success",),
        custom_field_first,
    ),
    ExecutionScenario(
        "exe-03-repeated-execution",
        "repeated_execution",
        "Executing the same version twice changes nothing the second time.",
        ("duplicate", "expect_success"),
        repeated_execution,
    ),
    ExecutionScenario(
        "exe-04-same-idempotency-key",
        "repeated_execution",
        "A retried request with the same Idempotency-Key returns the same run.",
        ("duplicate",),
        same_idempotency_key,
    ),
    ExecutionScenario(
        "exe-05-timeout-after-success",
        "save_timeout_after_success",
        "The save succeeds but the response is lost; state is read before retrying.",
        ("recovery", "expect_success"),
        timeout_after_success,
    ),
    ExecutionScenario(
        "exe-06-lost-write",
        "save_timeout_after_success",
        "The write is lost; after reading state it is retried exactly once.",
        ("recovery", "expect_success"),
        lost_write_retried_once,
    ),
    ExecutionScenario(
        "exe-07-ui-renamed",
        "ui_element_renamed",
        "A control is renamed; the run stops and nothing later is attempted.",
        (),
        ui_control_renamed,
    ),
    ExecutionScenario(
        "exe-08-crash-after-write",
        "worker_restart",
        "The executor dies right after a write; a new one resumes without repeating it.",
        ("recovery", "expect_success"),
        crash_after_write,
    ),
    ExecutionScenario(
        "exe-09-unauthorized-operation",
        "unauthorized_operation",
        "The connector is not authorized to write.",
        ("no_write",),
        connector_not_authorized,
    ),
    ExecutionScenario(
        "exe-10-superseded-version",
        "unauthorized_operation",
        "Executing a version that is no longer the approved one.",
        ("no_write",),
        superseded_version,
    ),
    ExecutionScenario(
        "exe-11-schema-changed",
        "schema_change_after_approval",
        "The firm's schema changed after approval.",
        ("no_write",),
        schema_changed_after_approval,
    ),
    ExecutionScenario(
        "exe-12-target-drift",
        "schema_change_after_approval",
        "A field the plan writes changed in the CMS since approval.",
        ("no_write",),
        target_drift,
    ),
    ExecutionScenario(
        "exe-13-unexpected-cms-field",
        "unexpected_cms_field",
        "An extra CMS field is reported and preserved.",
        ("expect_success",),
        unexpected_cms_field,
    ),
    ExecutionScenario(
        "exe-14-silent-wrong-save",
        "false_success_guard",
        "The CMS reports success but stores another value; never reported as success.",
        (),
        silent_wrong_save,
    ),
    ExecutionScenario(
        "exe-15-unmanaged-conflict",
        "unexpected_cms_field",
        "The target is already mapped from a key the plan does not manage.",
        (),
        unmanaged_mapping_conflict,
    ),
    ExecutionScenario(
        "exe-16-missing-required-source",
        "missing_required_source",
        "A CMS-required field has no source: onboarding marked incomplete.",
        ("expect_success",),
        missing_required_source,
    ),
]


def false_success(run: dict[str, Any], cms_config: Any) -> bool:
    """A run that says 'succeeded' while the stored configuration does not match its plan."""
    if run.get("status") != "succeeded":
        return False
    plan = [PlannedAction.model_validate(s["payload"]) for s in run["steps"]]
    return any(c.status != "match" for c in verify(plan, cms_config))


def run_scenario(scenario: ExecutionScenario, client: TestClient) -> dict[str, Any]:
    from app.main import app

    _current_cms.clear()
    try:
        outcome = scenario.run(client, scenario.id)
    except Exception as exc:  # a crashing scenario is a failed scenario, never a pass
        outcome = Outcome(False, {"error": f"{type(exc).__name__}: {exc}"[:500]})
    finally:
        app.dependency_overrides.clear()
    cms = _current_cms[0] if _current_cms else None
    false_successes = [r["id"] for r in outcome.runs if cms and false_success(r, cms.config)]
    return {
        "id": scenario.id,
        "category": scenario.category,
        "description": scenario.description,
        "tags": list(scenario.tags),
        "passed": outcome.passed and not false_successes,
        "observations": outcome.observations,
        "runs_reported_succeeded": sum(r.get("status") == "succeeded" for r in outcome.runs),
        "false_success_runs": false_successes,
    }
