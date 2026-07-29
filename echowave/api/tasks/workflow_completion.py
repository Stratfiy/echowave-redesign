from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.services.billing.tasks import cost_completed_workflow_run
from api.tasks.run_integrations import run_integrations_post_workflow_run


async def process_workflow_completion(
    _ctx,
    workflow_run_id: int,
):
    """Process workflow completion: run post-call integrations, then cost the call.

    Recording/transcript uploads happen in the pipeline process itself
    (api/services/workflow_run_artifacts.py) before this job is enqueued,
    so this task needs no shared filesystem with the web tier.

    Args:
        _ctx: ARQ context (unused)
        workflow_run_id: The workflow run ID
    """
    run_id = str(workflow_run_id)
    set_current_run_id(run_id)

    logger.info(f"Processing workflow completion for run {workflow_run_id}")

    # Run integrations including QA analysis (after uploads are complete)
    try:
        await run_integrations_post_workflow_run(_ctx, workflow_run_id)
    except Exception as e:
        logger.error(f"Error running integrations for workflow {workflow_run_id}: {e}")

    # Cost the call last: integrations can enrich usage_info (QA token usage is
    # recorded there), so costing after them prices the complete picture.
    # Isolated in its own try/except because a costing failure must not lose the
    # integration work that already succeeded — and costing is idempotent, so a
    # retry of this job is safe.
    try:
        await cost_completed_workflow_run(workflow_run_id)
    except Exception as e:
        logger.error(f"Error costing workflow run {workflow_run_id}: {e}")

    logger.info(f"Completed workflow completion processing for run {workflow_run_id}")
