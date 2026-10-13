"""Temporal activities: thin wrappers around the execution stages.

Each activity opens its own database session and reloads state by run id, so it can run on any
worker and be retried after a crash. Side-effect safety comes from the stages themselves
(read-before-write, attempt recorded before the write), not from Temporal.
"""

from collections.abc import Callable
from typing import Any

from temporalio import activity

from app.db.session import get_sessionmaker
from app.domain import execution_service
from app.integrations.registry import build_reader, build_writer


def _heartbeat() -> None:
    activity.heartbeat()


@activity.defn
def preflight_activity(run_id: int) -> bool:
    with get_sessionmaker()() as db:
        return execution_service.preflight(db, run_id, build_reader)


@activity.defn
def pending_steps_activity(run_id: int) -> list[int]:
    with get_sessionmaker()() as db:
        return execution_service.pending_steps(db, run_id)


@activity.defn
def execute_step_activity(run_id: int, sequence: int) -> str:
    activity.heartbeat()
    with get_sessionmaker()() as db:
        return execution_service.execute_step(
            db, run_id, sequence, build_reader, build_writer, heartbeat=_heartbeat
        )


@activity.defn
def finalize_activity(run_id: int) -> str:
    with get_sessionmaker()() as db:
        return execution_service.finalize(db, run_id, build_reader)


@activity.defn
def fail_run_activity(run_id: int, message: str) -> str:
    with get_sessionmaker()() as db:
        return execution_service.fail_run(db, run_id, "orchestration", message)


ACTIVITIES: list[Callable[..., Any]] = [
    preflight_activity,
    pending_steps_activity,
    execute_step_activity,
    finalize_activity,
    fail_run_activity,
]
