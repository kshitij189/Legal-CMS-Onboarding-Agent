"""Execute an approved mapping version and report read-after-write verification (blueprint §12).

The only input that can cause CMS writes is an approved mapping version id; there is no endpoint
that accepts arbitrary actions or browser instructions.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.deps import require
from app.config import get_settings
from app.db.session import get_db
from app.domain import execution_service, project_service
from app.domain.execution_service import ReaderFactory, WriterFactory
from app.errors import OrchestratorUnavailable
from app.integrations.registry import build_reader, build_writer
from app.models import OnboardingRun, User
from app.permissions import Permission
from app.workflows.client import start_run_workflow

router = APIRouter(prefix="/api/v1/onboarding-projects/{project_id}", tags=["execution"])


def get_reader_factory() -> ReaderFactory:
    return build_reader


def get_writer_factory() -> WriterFactory:
    return build_writer


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mapping_version_id: int
    channel: Literal["api", "browser"] = "browser"


class StepOut(BaseModel):
    sequence: int
    action_type: str
    subject: str
    status: str
    attempts: int
    error_category: str | None
    error_message: str | None
    evidence: dict[str, Any]
    payload: dict[str, Any]


class CheckOut(BaseModel):
    subject: str
    check_type: str
    status: str
    reason: str
    expected: dict[str, Any]
    actual: dict[str, Any] | None


class RunOut(BaseModel):
    id: int
    created: bool
    workflow_id: str | None
    mapping_version_id: int
    channel: str
    status: str
    error_category: str | None
    error_message: str | None
    drift_issues: list[dict[str, Any]]
    config_revision_before: int | None
    config_revision_after: int | None
    artifact_dir: str | None
    created_by: str
    started_at: datetime
    finished_at: datetime | None
    summary: dict[str, Any]
    steps: list[StepOut]
    verification: list[CheckOut]


def run_out(run: OnboardingRun, created: bool) -> RunOut:
    step_counts: dict[str, int] = {}
    for s in run.steps:
        step_counts[s.status] = step_counts.get(s.status, 0) + 1
    check_counts: dict[str, int] = {}
    for c in run.verification:
        check_counts[c.status] = check_counts.get(c.status, 0) + 1
    return RunOut(
        id=run.id,
        created=created,
        workflow_id=run.workflow_id,
        mapping_version_id=run.mapping_version_id,
        channel=run.channel,
        status=run.status,
        error_category=run.error_category,
        error_message=run.error_message,
        drift_issues=run.drift_issues,
        config_revision_before=run.config_revision_before,
        config_revision_after=run.config_revision_after,
        artifact_dir=run.artifact_dir,
        created_by=run.created_by.username,
        started_at=run.started_at,
        finished_at=run.finished_at,
        summary={"steps": step_counts, "verification": check_counts},
        steps=[
            StepOut(
                sequence=s.sequence,
                action_type=s.action_type,
                subject=s.subject,
                status=s.status,
                attempts=s.attempts,
                error_category=s.error_category,
                error_message=s.error_message,
                evidence=s.evidence,
                payload=s.payload,
            )
            for s in run.steps
        ],
        verification=[
            CheckOut(
                subject=c.subject,
                check_type=c.check_type,
                status=c.status,
                reason=c.reason,
                expected=c.expected,
                actual=c.actual,
            )
            for c in run.verification
        ],
    )


def get_run_dispatcher(
    reader_factory: ReaderFactory = Depends(get_reader_factory),
    writer_factory: WriterFactory = Depends(get_writer_factory),
) -> Callable[[Session, OnboardingRun], None]:
    """How a created run gets executed: a durable Temporal workflow, or inline in this process."""
    if get_settings().orchestrator == "inline":

        def inline(db: Session, run: OnboardingRun) -> None:
            execution_service.run_inline(db, run.id, reader_factory, writer_factory)

        return inline

    def temporal(db: Session, run: OnboardingRun) -> None:
        assert run.workflow_id is not None  # noqa: S101 - set at creation
        try:
            start_run_workflow(run.id, run.workflow_id)
        except Exception as exc:  # orchestrator unreachable: do not leave the project locked
            execution_service.fail_run(
                db, run.id, "orchestrator_unavailable", f"Could not start the run: {exc}"[:500]
            )
            raise OrchestratorUnavailable(
                "The run could not be started; nothing was written to the CMS.",
                [{"code": "orchestrator_unavailable", "run_id": run.id}],
            ) from None

    return temporal


@router.post("/execute", response_model=RunOut)
def execute(
    project_id: int,
    body: ExecuteRequest,
    response: Response,
    idempotency_key: str | None = Header(default=None, max_length=100),
    user: User = Depends(require(Permission.RUNS_EXECUTE)),
    db: Session = Depends(get_db),
    dispatch: Callable[[Session, OnboardingRun], None] = Depends(get_run_dispatcher),
) -> RunOut:
    """Create the run and hand it to the orchestrator. With Temporal this returns 202 while the run
    proceeds in the background; poll `GET /runs/{id}`. A repeated Idempotency-Key returns 200."""
    project = project_service.get_project(db, user, project_id)
    run, created = execution_service.create_run(
        db, user, project, body.mapping_version_id, body.channel, idempotency_key
    )
    if not created:
        response.status_code = 200
        return run_out(run, False)
    dispatch(db, run)
    db.refresh(run)
    response.status_code = 202 if run.status == "running" else 201
    return run_out(run, True)


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    project_id: int,
    user: User = Depends(require(Permission.PROJECTS_READ)),
    db: Session = Depends(get_db),
) -> list[RunOut]:
    project = project_service.get_project(db, user, project_id)
    return [run_out(r, False) for r in execution_service.list_runs(db, project)]


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(
    project_id: int,
    run_id: int,
    user: User = Depends(require(Permission.PROJECTS_READ)),
    db: Session = Depends(get_db),
) -> RunOut:
    project = project_service.get_project(db, user, project_id)
    return run_out(execution_service.get_run(db, project, run_id), False)


@router.get("/verification", response_model=RunOut)
def latest_verification(
    project_id: int,
    user: User = Depends(require(Permission.PROJECTS_READ)),
    db: Session = Depends(get_db),
) -> RunOut:
    """The most recent finished run with its expected-vs-actual checks (blueprint §12)."""
    project = project_service.get_project(db, user, project_id)
    return run_out(execution_service.get_run(db, project, None), False)
