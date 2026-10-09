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

    # First, before anything reads the run: the QA pass writes its summary
    # and extracted data from the logs, the webhooks deliver the gathered
    # context, and the thread row follows. An OTP or a card number the
    # caller gave is masked in all of them by being masked here once. The
    # pipeline already masked the events it saved; this catches the
    # gathered context, and runs that ended without the pipeline's handler.
    from api.services.privacy.masking import mask_completed_run

    await mask_completed_run(workflow_run_id)

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

    # The call's own line on the bot's thread. After costing so the row can
    # never be the reason a call went unpriced; guarded inside.
    from api.services.workflow import agent_timeline

    await agent_timeline.record_call_ended(workflow_run_id)

    # A medicine reminder call (launch stream care) settles its dose and,
    # when it was not taken, tells the family. Never raises; a run that was
    # not a reminder call is left alone.
    from api.services.care import calls as care_calls

    await care_calls.record_run_outcome(workflow_run_id)

    # A "call me when it's done" call (services/call_when_done) settles as
    # answered or not answered; not answered tells the person in the app.
    # Never raises; any other run is left alone.
    from api.services.call_when_done import calls as done_calls

    await done_calls.record_run_outcome(workflow_run_id)

    # A reminder call (services/reminder_calls) settles its attempt on what
    # the run shows and moves the reminder by what the person said. Never
    # raises; any other run is left alone.
    from api.services.reminder_calls import calls as reminder_calls

    await reminder_calls.record_run_outcome(workflow_run_id)

    logger.info(f"Completed workflow completion processing for run {workflow_run_id}")
