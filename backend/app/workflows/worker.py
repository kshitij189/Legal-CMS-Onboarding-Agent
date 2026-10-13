"""Temporal worker for onboarding runs.

Usage: python -m app.workflows.worker
On start-up it re-submits every run still marked "running" (e.g. the API crashed between saving
the run and starting its workflow). Starting is idempotent: runs whose workflow exists are left
alone, so recovery never creates a second execution.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
from temporalio.worker import Worker

from app.config import get_settings
from app.db.session import get_sessionmaker
from app.models import OnboardingRun
from app.observability.logging import configure_logging
from app.workflows.activities import ACTIVITIES
from app.workflows.client import connect, start_run_workflow_async
from app.workflows.onboarding_run import OnboardingRunWorkflow

log = logging.getLogger("app.workflows.worker")


async def recover_running_runs() -> list[int]:
    with get_sessionmaker()() as db:
        runs = list(
            db.execute(
                select(OnboardingRun.id, OnboardingRun.workflow_id).where(
                    OnboardingRun.status == "running", OnboardingRun.workflow_id.is_not(None)
                )
            )
        )
    client = await connect()
    restarted = []
    for run_id, workflow_id in runs:
        if await start_run_workflow_async(client, run_id, workflow_id):
            restarted.append(run_id)
    if restarted:
        log.warning("Recovered runs without a workflow: %s", restarted)
    return restarted


async def main() -> None:
    configure_logging()
    settings = get_settings()
    client = await connect()
    await recover_running_runs()
    # Activities are synchronous (SQLAlchemy, sync Playwright) and run in worker threads.
    with ThreadPoolExecutor(max_workers=4) as executor:
        worker = Worker(
            client,
            task_queue=settings.temporal_task_queue,
            workflows=[OnboardingRunWorkflow],
            activities=ACTIVITIES,
            activity_executor=executor,
        )
        log.info("Worker polling task queue %s", settings.temporal_task_queue)
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
