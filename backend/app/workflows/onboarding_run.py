"""Durable onboarding run (blueprint §9 "Temporal", Phase 5).

The workflow only sequences activities; all decisions about CMS state are made inside the
activities from fresh reads. If a worker dies, Temporal re-runs the interrupted activity on a live
worker (detected through missed heartbeats), and the stage recognises what already happened.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import (
        execute_step_activity,
        fail_run_activity,
        finalize_activity,
        pending_steps_activity,
        preflight_activity,
    )

# Bounded retries for infrastructure failures (worker crash, database blip). CMS outcomes such as
# validation errors or UI drift are not exceptions: the stage records them and returns a status.
RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
SHORT = timedelta(seconds=60)
STEP_TIMEOUT = timedelta(minutes=3)
# A dead worker stops heartbeating; the step is then retried elsewhere well before STEP_TIMEOUT.
STEP_HEARTBEAT = timedelta(seconds=20)


@workflow.defn
class OnboardingRunWorkflow:
    @workflow.run
    async def run(self, run_id: int) -> str:
        try:
            ready = await workflow.execute_activity(
                preflight_activity, run_id, start_to_close_timeout=SHORT, retry_policy=RETRY
            )
            if ready:
                sequences = await workflow.execute_activity(
                    pending_steps_activity,
                    run_id,
                    start_to_close_timeout=SHORT,
                    retry_policy=RETRY,
                )
                for sequence in sequences:
                    status = await workflow.execute_activity(
                        execute_step_activity,
                        args=[run_id, sequence],
                        start_to_close_timeout=STEP_TIMEOUT,
                        heartbeat_timeout=STEP_HEARTBEAT,
                        retry_policy=RETRY,
                    )
                    if status == "failed":
                        break
            return await workflow.execute_activity(
                finalize_activity, run_id, start_to_close_timeout=SHORT, retry_policy=RETRY
            )
        except ActivityError as exc:
            # Retries exhausted: record the failure instead of leaving the run "running".
            return await workflow.execute_activity(
                fail_run_activity,
                args=[run_id, f"Orchestration gave up: {exc.cause or exc}"[:500]],
                start_to_close_timeout=SHORT,
                retry_policy=RETRY,
            )
