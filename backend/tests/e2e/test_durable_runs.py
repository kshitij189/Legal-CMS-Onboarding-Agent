"""Durable runs (Phase 5): real Temporal, real worker processes, real mock CMS.

Needs the Compose Temporal dev server on localhost:7234 (`docker compose up -d temporal`) and the
e2e harness (mock CMS processes). Each test uses its own task queue, so leftover tasks from other
runs can never be picked up. Workers run as separate processes so they can be killed for real.
"""

import asyncio
import os
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio.client import WorkflowExecutionStatus

from app.api.execution import get_run_dispatcher
from app.config import REPO_ROOT, get_settings
from app.main import app
from app.models import ActionStep
from app.workflows.client import connect, start_run_workflow
from tests.e2e.harness import V1, _approved, _cms_config
from tests.integration.conftest import FirmContext

pytestmark = pytest.mark.e2e

TEMPORAL = os.environ.get("ONBOARDING_TEMPORAL_ADDRESS", "localhost:7234")
BACKEND_PYTHON = (
    REPO_ROOT / "backend" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
)


@pytest.fixture
def temporal(cms: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Route the API to Temporal on a fresh task queue for this test."""
    host, port = TEMPORAL.split(":")
    try:
        socket.create_connection((host, int(port)), timeout=2).close()
    except OSError:
        pytest.skip(f"Temporal not reachable at {TEMPORAL} (docker compose up -d temporal)")
    queue = f"onboarding-test-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("ONBOARDING_ORCHESTRATOR", "temporal")
    monkeypatch.setenv("ONBOARDING_TEMPORAL_ADDRESS", TEMPORAL)
    monkeypatch.setenv("ONBOARDING_TEMPORAL_TASK_QUEUE", queue)
    get_settings.cache_clear()
    yield queue
    get_settings.cache_clear()


class Workers:
    """Starts worker processes with the test's environment; kills leftovers at teardown."""

    def __init__(self) -> None:
        self.processes: list[subprocess.Popen[bytes]] = []

    def start(self, **extra_env: str) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(  # noqa: S603 - fixed arguments, test harness only
            [str(BACKEND_PYTHON), "-m", "app.workflows.worker"],
            cwd=REPO_ROOT / "backend",
            env={**os.environ, **extra_env},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.processes.append(process)
        return process

    def stop_all(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)


@pytest.fixture
def workers(temporal: str) -> Iterator[Workers]:
    pool = Workers()
    yield pool
    pool.stop_all()


def _execute(
    client: TestClient, ctx: FirmContext, version_id: int, channel: str, key: str | None = None
) -> Any:
    headers = {**ctx.specialist, **({"Idempotency-Key": key} if key else {})}
    return client.post(
        f"/api/v1/onboarding-projects/{ctx.project_id}/execute",
        headers=headers,
        json={"mapping_version_id": version_id, "channel": channel},
    )


def _wait_for_run(
    client: TestClient, ctx: FirmContext, run_id: int, timeout: float = 180
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run: dict[str, Any] = client.get(
            f"/api/v1/onboarding-projects/{ctx.project_id}/runs/{run_id}",
            headers=ctx.specialist,
        ).json()
        if run["status"] != "running":
            return run
        time.sleep(1)
    pytest.fail(f"run {run_id} still running after {timeout}s")


def _workflow_status(workflow_id: str) -> WorkflowExecutionStatus | None:
    async def describe() -> WorkflowExecutionStatus | None:
        client = await connect()
        return (await client.get_workflow_handle(workflow_id).describe()).status

    return asyncio.run(describe())


def test_run_executes_durably_in_the_background(client: TestClient, workers: Workers) -> None:
    ctx, version = _approved(client, "firm-a", V1)
    workers.start()
    response = _execute(client, ctx, version["id"], "api")
    assert response.status_code == 202, response.text  # accepted; the worker does the work
    started = response.json()
    assert started["status"] == "running" and started["workflow_id"]

    run = _wait_for_run(client, ctx, started["id"])
    assert run["status"] == "succeeded", run
    assert run["summary"] == {"steps": {"applied": 8}, "verification": {"match": 8}}
    assert _workflow_status(started["workflow_id"]) == WorkflowExecutionStatus.COMPLETED


def test_killed_worker_resumes_without_repeating_the_write(
    client: TestClient, workers: Workers, db: Session
) -> None:
    """The worker dies right after creating the custom field, before recording it. A new worker
    must finish the run without creating the field again (a second create would be rejected)."""
    ctx, version = _approved(client, "firm-c", V1)
    doomed = workers.start(ONBOARDING_FAULT_CRASH_AFTER_WRITE="matter.custom.review_status")
    started = _execute(client, ctx, version["id"], "browser").json()

    assert doomed.wait(timeout=120) == 70, "worker should crash after the write"
    first = db.scalar(
        select(ActionStep).where(ActionStep.run_id == started["id"], ActionStep.sequence == 1)
    )
    assert first is not None
    assert (first.status, first.attempts) == ("pending", 1)  # attempt recorded, outcome not
    fields = {f["key"] for f in _cms_config(V1, "c")["fields"]}
    assert "matter.custom.review_status" in fields  # the side effect did happen

    workers.start()  # a healthy worker picks the run up after the missed heartbeat
    run = _wait_for_run(client, ctx, started["id"])
    assert run["status"] == "succeeded", run
    step = run["steps"][0]
    assert step["subject"] == "matter.custom.review_status"
    assert (step["status"], step["attempts"]) == ("applied", 1)  # never written a second time
    assert step["evidence"]["after_interruption"] is True
    # Plan: 1 custom field + 8 mappings (ambiguous "notes" was rejected in review).
    assert run["summary"]["verification"] == {"match": 9}
    # 9 effective changes, so revision 9: nothing was applied twice.
    assert _cms_config(V1, "c")["config_revision"] == 9


def test_duplicate_execution_is_prevented(client: TestClient, workers: Workers) -> None:
    ctx, version = _approved(client, "firm-a", V1)
    workers.start()
    first = _execute(client, ctx, version["id"], "api", key="exec-1").json()
    again = _execute(client, ctx, version["id"], "api", key="exec-1")
    assert again.status_code == 200 and again.json()["id"] == first["id"]
    # A second start for the same run (API retry, recovery) is rejected by Temporal.
    assert start_run_workflow(first["id"], first["workflow_id"]) is False
    run = _wait_for_run(client, ctx, first["id"])
    assert run["status"] == "succeeded"
    assert start_run_workflow(first["id"], first["workflow_id"]) is False  # completed: no rerun
    revision = _cms_config(V1, "a")["config_revision"]

    # A new run of the same version changes nothing in the CMS.
    second = _execute(client, ctx, version["id"], "api").json()
    rerun = _wait_for_run(client, ctx, second["id"])
    assert rerun["summary"]["steps"] == {"already_applied": 8}
    assert _cms_config(V1, "a")["config_revision"] == revision


def test_worker_start_recovers_runs_that_never_got_a_workflow(
    client: TestClient, workers: Workers
) -> None:
    """Simulates the API crashing between saving the run and starting its workflow."""
    ctx, version = _approved(client, "firm-a", V1)
    app.dependency_overrides[get_run_dispatcher] = lambda: lambda db, run: None
    orphan = _execute(client, ctx, version["id"], "api").json()
    del app.dependency_overrides[get_run_dispatcher]
    assert orphan["status"] == "running"
    assert _workflow_status_or_none(orphan["workflow_id"]) is None

    workers.start()  # start-up recovery submits the orphaned run
    run = _wait_for_run(client, ctx, orphan["id"])
    assert run["status"] == "succeeded", run


def _workflow_status_or_none(workflow_id: str) -> WorkflowExecutionStatus | None:
    try:
        return _workflow_status(workflow_id)
    except Exception:  # noqa: BLE001 - "not found" is the expected outcome here
        return None
