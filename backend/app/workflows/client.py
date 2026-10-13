"""Starting durable runs from synchronous code (API handlers, worker start-up recovery)."""

import asyncio

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.config import get_settings
from app.workflows.onboarding_run import OnboardingRunWorkflow


async def connect() -> Client:
    settings = get_settings()
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


async def start_run_workflow_async(client: Client, run_id: int, workflow_id: str) -> bool:
    """Start the workflow for a run. Returns False if it already exists (idempotent start)."""
    try:
        await client.start_workflow(
            OnboardingRunWorkflow.run,
            run_id,
            id=workflow_id,
            task_queue=get_settings().temporal_task_queue,
            # A workflow id is used once; only a failed attempt may be started again (recovery).
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
        )
    except WorkflowAlreadyStartedError:
        return False
    return True


def start_run_workflow(run_id: int, workflow_id: str) -> bool:
    async def _start() -> bool:
        return await start_run_workflow_async(await connect(), run_id, workflow_id)

    return asyncio.run(_start())
