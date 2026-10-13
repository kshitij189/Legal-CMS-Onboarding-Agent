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
"""

import logging
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
    CMSConfiguration,
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


def start_run(
    db: Session,
    user: User,
    project: OnboardingProject,
    mapping_version_id: int,
    channel: str,
    idempotency_key: str | None,
    reader_factory: ReaderFactory,
    writer_factory: WriterFactory,
) -> tuple[OnboardingRun, bool]:
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

    with tracer.start_as_current_span(
        "execute_run", attributes={"onboarding.run_id": run.id, "execution.channel": channel}
    ):
        try:
            _execute(
                db,
                user,
                project,
                run,
                plan,
                _schema(db, version.target_schema_id),
                reader_factory,
                writer_factory,
            )
        except Exception:  # never leave a run silently "running"
            log.exception("Run %s crashed", run.id)
            db.rollback()
            _finish(db, user, project, run, "failed", "internal", "Unexpected executor error.")
    db.refresh(run)
    return run, True


def _apply(writer: ConfigurationWriter, action: PlannedAction) -> WriteOutcome:
    if action.custom_field is not None:
        return writer.create_custom_field(action.custom_field)
    assert action.mapping is not None  # noqa: S101
    return writer.upsert_mapping(action.mapping)


def _execute(
    db: Session,
    user: User,
    project: OnboardingProject,
    run: OnboardingRun,
    plan: list[PlannedAction],
    target_schema: SchemaDefinition,
    reader_factory: ReaderFactory,
    writer_factory: WriterFactory,
) -> None:
    steps = {
        s.sequence: s for s in db.scalars(select(ActionStep).where(ActionStep.run_id == run.id))
    }
    try:
        reader = reader_factory(project.connector)
        config = reader.read_configuration()
    except ConnectorError as exc:
        _finish(db, user, project, run, "failed", exc.category, exc.message)
        return
    run.config_revision_before = config.config_revision

    drift = check_drift(target_schema.to_normalized(), config, plan)
    run.drift_issues = [d.model_dump(mode="json") for d in drift]
    if any(d.blocking for d in drift):
        for step in steps.values():
            step.status = "skipped"
        _finish(
            db,
            user,
            project,
            run,
            "blocked",
            "target_drift",
            "The CMS changed since approval; inspect the target and re-approve.",
        )
        return

    relative_dir = Path("runs") / str(run.id)
    artifacts_dir = get_settings().artifacts_dir / relative_dir
    failure: ConnectorError | None = None
    writer = writer_factory(project.connector, run.channel, artifacts_dir)
    try:
        with writer:
            for action in plan:
                step = steps[action.sequence]
                failure, config = _run_step(db, step, action, writer, reader, config)
                if failure is not None:
                    break
    except ConnectorError as exc:  # session-level failures, e.g. browser login refused
        failure = exc
    except Exception as exc:  # browser/driver crashes are failures, never "probably fine"
        log.exception("Writer crashed in run %s", run.id)
        failure = ConnectorError("browser_error", f"Writer error: {type(exc).__name__}.")
    saved = list(getattr(writer, "saved_artifacts", []))
    if saved:
        run.artifact_dir = relative_dir.as_posix()
    blocked = list(getattr(writer, "blocked_requests", []))
    for step in steps.values():
        if step.status == "pending" and step.attempts > 0 and failure is not None:
            # Attempted when the session failed (e.g. read-back error): never report "skipped".
            step.status = "failed"
            step.error_category = failure.category
            step.error_message = failure.message
        elif step.status == "pending":
            step.status = "skipped"
        if step.status == "failed" and (saved or blocked):
            step.evidence = {**step.evidence, "artifacts": saved, "blocked_requests": blocked}
    db.commit()

    # Read-after-write verification decides the outcome, even after a failure (to report state).
    try:
        final = reader.read_configuration()
    except ConnectorError as exc:
        _finish(
            db,
            user,
            project,
            run,
            "failed",
            exc.category,
            f"Could not read back configuration: {exc.message}",
        )
        return
    run.config_revision_after = final.config_revision
    for check in verify(plan, final):
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
    db.flush()
    if failure is not None:
        _finish(db, user, project, run, "failed", failure.category, failure.message)
    elif any(
        r.status != "match"
        for r in db.scalars(select(VerificationResult).where(VerificationResult.run_id == run.id))
    ):
        _finish(
            db,
            user,
            project,
            run,
            "verification_failed",
            "verification",
            "The stored configuration differs from the approved plan.",
        )
    else:
        _finish(db, user, project, run, "succeeded", None, None)


def _run_step(
    db: Session,
    step: ActionStep,
    action: PlannedAction,
    writer: ConfigurationWriter,
    reader: ConfigurationReader,
    config: CMSConfiguration,
) -> tuple[ConnectorError | None, CMSConfiguration]:
    with tracer.start_as_current_span(
        "action_step",
        attributes={"step.sequence": step.sequence, "step.action": step.action_type},
    ):
        step.started_at = utcnow()
        state = action_state(action, config)
        if state == "done":
            step.status = "already_applied"
            step.evidence = {"reason": "state already matched before writing"}
            step.finished_at = utcnow()
            db.commit()
            return None, config
        if state == "conflict":
            failure = ConnectorError(
                "conflict", "The CMS holds a different value that this plan may not overwrite."
            )
            _fail_step(db, step, failure)
            return failure, config

        while True:
            step.attempts += 1
            try:
                outcome = _apply(writer, action)
            except ConnectorError as exc:
                if exc.category not in RETRY_AFTER_READBACK:
                    _fail_step(db, step, exc)
                    return exc, config
                # Unknown outcome: look before leaping.
                config = reader.read_configuration()
                if action_state(action, config) == "done":
                    step.status = "applied"
                    step.evidence = {"resolved_by_read_back": True, "first_error": exc.category}
                    break
                if step.attempts >= MAX_WRITE_ATTEMPTS:
                    _fail_step(db, step, exc)
                    return exc, config
                continue
            step.status = "applied"
            step.evidence = outcome.evidence | {"changed": outcome.changed}
            config = reader.read_configuration()
            break
        step.finished_at = utcnow()
        db.commit()
        return None, config


def _fail_step(db: Session, step: ActionStep, error: ConnectorError) -> None:
    step.status = "failed"
    step.error_category = error.category
    step.error_message = error.message
    step.evidence = {**step.evidence, "details": error.details}
    step.finished_at = utcnow()
    db.commit()


def _finish(
    db: Session,
    user: User,
    project: OnboardingProject,
    run: OnboardingRun,
    status: str,
    error_category: str | None,
    error_message: str | None,
) -> None:
    run.status = status
    run.error_category = error_category
    run.error_message = error_message
    run.finished_at = utcnow()
    steps = list(db.scalars(select(ActionStep).where(ActionStep.run_id == run.id)))
    counts: dict[str, int] = {}
    for s in steps:
        counts[s.status] = counts.get(s.status, 0) + 1
    audit.record(
        db,
        firm_id=project.firm_id,
        project_id=project.id,
        actor=user,
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
