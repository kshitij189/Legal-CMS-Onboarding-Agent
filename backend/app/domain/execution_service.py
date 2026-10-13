"""Executes an approved mapping version against the firm's CMS, then verifies by reading back.

Safety rules (blueprint §10, §14; CLAUDE.md §11):
- Only an *approved, current* mapping version whose schema versions are still the latest can run.
- At most one running run per project (database partial unique index).
- Pre-flight drift check against the target schema the approval was based on; blocking drift
  stops the run before any write.
- Each step reads state first: already-applied steps are not repeated (idempotent re-runs).
- A write with an unknown outcome is never blindly retried: state is read back first, and only if
  the change is absent is it attempted once more.
- The first failure stops the run; later steps are skipped, never improvised.
- Success is decided by read-after-write verification, not by the writer's own report.

Execution is split into stages (create_run -> preflight -> execute_step per step -> finalize) that
reload their state by run id, so a durable orchestrator (Temporal, Phase 5) can run each stage as
an activity and retry it after a crash without repeating completed side effects.
"""

import logging
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from opentelemetry import trace
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain import audit
from app.domain.execution_plan import action_state, build_plan, check_drift, verify
from app.domain.project_service import latest_schema
from app.errors import Conflict, ConnectorError, Forbidden, NotFound, ValidationFailed
from app.integrations.base import ConfigurationReader, ConfigurationWriter
from app.models import (
    ActionStep,
    MappingVersion,
    OnboardingProject,
    OnboardingRun,
    SchemaDefinition,
    User,
    VerificationResult,
    utcnow,
)
from app.schemas.execution import (
    PlannedAction,
    WriteOutcome,
)
from app.schemas.normalized import SchemaKind

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

ReaderFactory = Callable[[Any], ConfigurationReader]
WriterFactory = Callable[[Any, str, Path], ConfigurationWriter]
MAX_WRITE_ATTEMPTS = 2
RETRY_AFTER_READBACK = {"uncertain", "transient"}


def _schema(db: Session, schema_id: int) -> SchemaDefinition:
    schema = db.get(SchemaDefinition, schema_id)
    assert schema is not None  # noqa: S101 - FK guarantees
    return schema


def create_run(
    db: Session,
    user: User,
    project: OnboardingProject,
    mapping_version_id: int,
    channel: str,
    idempotency_key: str | None,
) -> tuple[OnboardingRun, bool]:
    """Validate and persist a run with its full plan. Writes nothing to the CMS."""
    if idempotency_key:
        existing = db.scalar(
            select(OnboardingRun).where(
                OnboardingRun.project_id == project.id,
                OnboardingRun.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing, False

    version = db.scalar(
        select(MappingVersion).where(
            MappingVersion.id == mapping_version_id, MappingVersion.project_id == project.id
        )
    )
    if version is None:
        raise NotFound(f"Mapping version {mapping_version_id} not found.")
    if version.status != "approved":
        raise Conflict(
            "Only the current approved mapping version can be executed.",
            [{"code": "version_not_current", "status": version.status}],
        )
    source = latest_schema(db, project, SchemaKind.SOURCE)
    target = latest_schema(db, project, SchemaKind.TARGET)
    if (
        source is None
        or target is None
        or (source.id, target.id) != (version.source_schema_id, version.target_schema_id)
    ):
        raise Conflict(
            "A schema changed after approval; propose, review and approve again.",
            [{"code": "schema_changed"}],
        )
    needed = {"read_configuration", f"write_{channel}"}
    missing = sorted(needed - set(project.connector.supported_operations))
    if missing:
        raise Forbidden(
            "The connector is not authorized for this operation.",
            [{"code": "operation_not_permitted", "operations": missing}],
        )

    plan = build_plan(version.items, _schema(db, version.source_schema_id).to_normalized())
    if not plan:
        raise ValidationFailed(
            "The approved version contains no actions.", [{"code": "empty_plan"}]
        )

    run = OnboardingRun(
        firm_id=project.firm_id,
        project_id=project.id,
        mapping_version_id=version.id,
        channel=channel,
        status="running",
        idempotency_key=idempotency_key,
        workflow_id=f"onboarding-run-{uuid.uuid4()}",
        created_by_id=user.id,
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict(
            "Another run is already in progress for this project.", [{"code": "run_in_progress"}]
        ) from None
    for action in plan:
        db.add(
            ActionStep(
                firm_id=run.firm_id,
                run_id=run.id,
                sequence=action.sequence,
                action_type=action.action_type.value,
                subject=action.subject,
                payload=action.model_dump(mode="json"),
                idempotency_key=f"run-{run.id}-step-{action.sequence}",
                status="pending",
            )
        )
    audit.record(
        db,
        firm_id=project.firm_id,
        project_id=project.id,
        actor=user,
        action="run.started",
        entity_type="onboarding_run",
        entity_id=run.id,
        details={"mapping_version": version.version, "channel": channel, "steps": len(plan)},
    )
    # Persist the plan before any side effect, so an interruption leaves a full record.
    db.commit()
    return run, True


# --- stages -------------------------------------------------------------------------------------
# Each stage loads everything it needs by run id and is safe to call again (Temporal retries an
# activity after a worker crash): finished work is recognised from the database or the CMS.


def _load_run(db: Session, run_id: int) -> OnboardingRun:
    run = db.get(OnboardingRun, run_id)
    if run is None:
        raise NotFound(f"Onboarding run {run_id} not found.")
    return run


def _project(db: Session, run: OnboardingRun) -> OnboardingProject:
    project = db.get(OnboardingProject, run.project_id)
    assert project is not None  # noqa: S101 - FK guarantees
    return project


def _plan(db: Session, run: OnboardingRun) -> dict[int, PlannedAction]:
    return {
        s.sequence: PlannedAction.model_validate(s.payload)
        for s in db.scalars(select(ActionStep).where(ActionStep.run_id == run.id))
    }


def preflight(db: Session, run_id: int, reader_factory: ReaderFactory) -> bool:
    """Read the CMS and check drift. Returns False if the run must not write anything."""
    run = _load_run(db, run_id)
    if run.status != "running":
        return False
    if run.config_revision_before is not None:  # already passed pre-flight before an interruption
        return True
    project = _project(db, run)
    try:
        config = reader_factory(project.connector).read_configuration()
    except ConnectorError as exc:
        _skip_pending(db, run)
        _finish(db, run, "failed", exc.category, exc.message)
        return False
    version = db.get(MappingVersion, run.mapping_version_id)
    assert version is not None  # noqa: S101
    drift = check_drift(
        _schema(db, version.target_schema_id).to_normalized(),
        config,
        list(_plan(db, run).values()),
    )
    run.drift_issues = [d.model_dump(mode="json") for d in drift]
    if any(d.blocking for d in drift):
        _skip_pending(db, run)
        _finish(
            db,
            run,
            "blocked",
            "target_drift",
            "The CMS changed since approval; inspect the target and re-approve.",
        )
        return False
    run.config_revision_before = config.config_revision
    db.commit()
    return True


def pending_steps(db: Session, run_id: int) -> list[int]:
    return list(
        db.scalars(
            select(ActionStep.sequence)
            .where(ActionStep.run_id == run_id, ActionStep.status == "pending")
            .order_by(ActionStep.sequence)
        )
    )


def _maybe_crash_after_write(run: OnboardingRun, step: ActionStep) -> None:
    """Fault injection for recovery tests only: kill the process right after a successful write,
    before the outcome is recorded — the worst moment for a crash. Fires once per step."""
    if os.environ.get("ONBOARDING_FAULT_CRASH_AFTER_WRITE") != step.subject:
        return
    marker = get_settings().artifacts_dir / "runs" / str(run.id) / f"crashed-step-{step.sequence}"
    if marker.exists():
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("crashed after write\n", encoding="utf-8")
    log.warning("Fault injection: crashing after write of step %s", step.sequence)
    os._exit(70)


def execute_step(
    db: Session,
    run_id: int,
    sequence: int,
    reader_factory: ReaderFactory,
    writer_factory: WriterFactory,
    heartbeat: Callable[[], None] = lambda: None,
) -> str:
    """Apply one planned action and return the step's final status."""
    run = _load_run(db, run_id)
    step = db.scalar(
        select(ActionStep).where(ActionStep.run_id == run_id, ActionStep.sequence == sequence)
    )
    if step is None:
        raise NotFound(f"Step {sequence} of run {run_id} not found.")
    if step.status != "pending":
        return step.status  # finished before an interruption: never redo
    if run.status != "running":
        step.status = "skipped"
        db.commit()
        return step.status
    project = _project(db, run)
    action = PlannedAction.model_validate(step.payload)
    with tracer.start_as_current_span(
        "action_step",
        attributes={
            "onboarding.run_id": run.id,
            "step.sequence": step.sequence,
            "step.action": step.action_type,
        },
    ):
        reader = reader_factory(project.connector)
        try:
            config = reader.read_configuration()
        except ConnectorError as exc:
            _fail_step(db, step, exc)
            return step.status
        heartbeat()
        state = action_state(action, config)
        if state == "done":
            step.started_at = step.started_at or utcnow()
            if step.attempts > 0:
                # A previous attempt wrote, then was interrupted before recording the outcome.
                step.status = "applied"
                step.evidence = {
                    "resolved_by_read_back": True,
                    "after_interruption": True,
                    "previous_attempts": step.attempts,
                }
            else:
                step.status = "already_applied"
                step.evidence = {"reason": "state already matched before writing"}
            step.finished_at = utcnow()
            db.commit()
            return step.status
        if state == "conflict":
            _fail_step(
                db,
                step,
                ConnectorError(
                    "conflict", "The CMS holds a different value that this plan may not overwrite."
                ),
            )
            return step.status

        relative_dir = Path("runs") / str(run.id)
        writer = writer_factory(
            project.connector, run.channel, get_settings().artifacts_dir / relative_dir
        )
        try:
            with writer:
                _write_with_readback(db, run, step, action, writer, reader, heartbeat)
        except ConnectorError as exc:  # session-level, e.g. browser login refused
            if step.status == "pending":
                _fail_step(db, step, exc)
        except Exception as exc:  # driver crashes are failures, never "probably fine"
            log.exception("Writer crashed in run %s step %s", run.id, step.sequence)
            if step.status == "pending":
                _fail_step(
                    db,
                    step,
                    ConnectorError("browser_error", f"Writer error: {type(exc).__name__}."),
                )
        saved = list(getattr(writer, "saved_artifacts", []))
        blocked = list(getattr(writer, "blocked_requests", []))
        if saved:
            run.artifact_dir = relative_dir.as_posix()
        if step.status == "failed" and (saved or blocked):
            step.evidence = {**step.evidence, "artifacts": saved, "blocked_requests": blocked}
        db.commit()
        return step.status


def _apply(writer: ConfigurationWriter, action: PlannedAction) -> WriteOutcome:
    if action.custom_field is not None:
        return writer.create_custom_field(action.custom_field)
    assert action.mapping is not None  # noqa: S101
    return writer.upsert_mapping(action.mapping)


def _write_with_readback(
    db: Session,
    run: OnboardingRun,
    step: ActionStep,
    action: PlannedAction,
    writer: ConfigurationWriter,
    reader: ConfigurationReader,
    heartbeat: Callable[[], None],
) -> None:
    while True:
        # Record the attempt *before* the side effect: after a crash, a resumed step knows a
        # write may have happened and checks the CMS instead of blindly writing again.
        step.attempts += 1
        step.started_at = step.started_at or utcnow()
        db.commit()
        try:
            outcome = _apply(writer, action)
        except ConnectorError as exc:
            if exc.category not in RETRY_AFTER_READBACK:
                _fail_step(db, step, exc)
                return
            heartbeat()
            config = reader.read_configuration()  # unknown outcome: look before leaping
            if action_state(action, config) == "done":
                step.status = "applied"
                step.evidence = {"resolved_by_read_back": True, "first_error": exc.category}
                break
            if step.attempts >= MAX_WRITE_ATTEMPTS:
                _fail_step(db, step, exc)
                return
            continue
        _maybe_crash_after_write(run, step)
        step.status = "applied"
        step.evidence = outcome.evidence | {"changed": outcome.changed}
        break
    step.finished_at = utcnow()
    db.commit()


def _fail_step(db: Session, step: ActionStep, error: ConnectorError) -> None:
    step.status = "failed"
    step.error_category = error.category
    step.error_message = error.message
    step.evidence = {**step.evidence, "details": error.details}
    step.finished_at = utcnow()
    db.commit()


def _skip_pending(db: Session, run: OnboardingRun) -> None:
    for step in db.scalars(
        select(ActionStep).where(ActionStep.run_id == run.id, ActionStep.status == "pending")
    ):
        step.status = "skipped"


def finalize(db: Session, run_id: int, reader_factory: ReaderFactory) -> str:
    """Read back, verify, decide the run outcome. Atomic: results and status commit together."""
    run = _load_run(db, run_id)
    if run.status != "running":
        return run.status
    _skip_pending(db, run)
    steps = list(db.scalars(select(ActionStep).where(ActionStep.run_id == run.id)))
    failed = next(
        (s for s in sorted(steps, key=lambda s: s.sequence) if s.status == "failed"), None
    )
    project = _project(db, run)
    try:
        final = reader_factory(project.connector).read_configuration()
    except ConnectorError as exc:
        _finish(
            db, run, "failed", exc.category, f"Could not read back configuration: {exc.message}"
        )
        return run.status
    run.config_revision_after = final.config_revision
    checks = verify(list(_plan(db, run).values()), final)
    for check in checks:
        db.add(
            VerificationResult(
                firm_id=run.firm_id,
                run_id=run.id,
                subject=check.subject,
                check_type=check.check_type,
                expected=check.expected,
                actual=check.actual,
                status=check.status,
                reason=check.reason,
            )
        )
    if failed is not None:
        _finish(db, run, "failed", failed.error_category, failed.error_message)
    elif any(c.status != "match" for c in checks):
        _finish(
            db,
            run,
            "verification_failed",
            "verification",
            "The stored configuration differs from the approved plan.",
        )
    else:
        _finish(db, run, "succeeded", None, None)
    return run.status


def fail_run(db: Session, run_id: int, category: str, message: str) -> str:
    """Last resort when orchestration gives up: never leave a run "running" forever."""
    run = _load_run(db, run_id)
    if run.status == "running":
        _skip_pending(db, run)
        _finish(db, run, "failed", category, message)
    return run.status


def run_inline(
    db: Session, run_id: int, reader_factory: ReaderFactory, writer_factory: WriterFactory
) -> str:
    """Execute all stages in this process (tests, and environments without Temporal)."""
    with tracer.start_as_current_span("execute_run", attributes={"onboarding.run_id": run_id}):
        try:
            if preflight(db, run_id, reader_factory):
                for sequence in pending_steps(db, run_id):
                    if (
                        execute_step(db, run_id, sequence, reader_factory, writer_factory)
                        == "failed"
                    ):
                        break
            return finalize(db, run_id, reader_factory)
        except Exception:
            log.exception("Run %s crashed", run_id)
            db.rollback()
            return fail_run(db, run_id, "internal", "Unexpected executor error.")


def _finish(
    db: Session,
    run: OnboardingRun,
    status: str,
    error_category: str | None,
    error_message: str | None,
) -> None:
    run.status = status
    run.error_category = error_category
    run.error_message = error_message
    run.finished_at = utcnow()
    db.flush()
    counts: dict[str, int] = {}
    for s in db.scalars(select(ActionStep).where(ActionStep.run_id == run.id)):
        counts[s.status] = counts.get(s.status, 0) + 1
    actor = db.get(User, run.created_by_id)
    audit.record(
        db,
        firm_id=run.firm_id,
        project_id=run.project_id,
        actor=actor,
        action=f"run.{status}",
        entity_type="onboarding_run",
        entity_id=run.id,
        details={
            "steps": counts,
            "error_category": error_category,
            "config_revision_before": run.config_revision_before,
            "config_revision_after": run.config_revision_after,
        },
    )
    db.commit()


def list_runs(db: Session, project: OnboardingProject) -> list[OnboardingRun]:
    return list(
        db.scalars(
            select(OnboardingRun)
            .where(OnboardingRun.project_id == project.id)
            .order_by(OnboardingRun.id.desc())
        )
    )


def get_run(db: Session, project: OnboardingProject, run_id: int | None) -> OnboardingRun:
    query = select(OnboardingRun).where(OnboardingRun.project_id == project.id)
    if run_id is not None:
        query = query.where(OnboardingRun.id == run_id)
    else:
        query = query.where(OnboardingRun.status != "running")
    run = db.scalar(query.order_by(OnboardingRun.id.desc()).limit(1))
    if run is None:
        raise NotFound("No onboarding run found.")
    return run
