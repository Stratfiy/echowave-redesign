"""What a placed call's own run proves about it, read before anybody is told.

The reminder paths (``services/care/calls``, ``services/call_when_done/calls``)
used to read the *absence* of a post-call report as "not answered". Absence
proves nothing: the carrier may not have called back yet, the post-call job
may be slow, or the dial may have timed out on our side after the carrier
accepted it. This module turns a workflow run into the evidence it actually
holds, and nothing more:

* ``ANSWERED``     -- somebody was on the line (``answered.was_answered``:
  the carrier's answered stamp, or billable seconds).
* ``NOT_CONNECTED`` -- the carrier's status callback said no-answer, busy or
  cancelled (``status_processor`` tags the run ``not_connected`` and stamps
  the disposition). A verified "did not pick up".
* ``CARRIER_FAILED`` -- the carrier said failed / error: the call was not
  connected for a reason that is not the person's.
* ``PENDING``      -- the run exists and has said nothing final. Still in
  flight, or its callback has not arrived: **unknown**, never "not answered".
* ``MISSING``      -- no run at all under that id.

The run's status fields are written only by the telephony status webhooks
(signature-checked per provider) and by the pipeline itself, so reading them
is reading authenticated evidence. Asking the provider directly
(``provider.get_call_status``) is not done here: every provider answers in
its own shape and none is exercised in tests; see docs/plans/reminder-calls.md.
"""

from __future__ import annotations

from typing import Any

from api.services.workflow import answered

ANSWERED = "answered"
NOT_CONNECTED = "not_connected"
CARRIER_FAILED = "carrier_failed"
PENDING = "pending"
MISSING = "missing"

#: Final evidence: a later reading cannot say anything different.
FINAL = frozenset({ANSWERED, NOT_CONNECTED, CARRIER_FAILED})

_NOT_CONNECTED = frozenset({"no-answer", "no_answer", "busy", "canceled", "cancelled"})
_CARRIER_FAILED = frozenset({"failed", "error"})


def classify(run: Any) -> str:
    """The evidence one run holds about its call (see the module docstring)."""
    if run is None:
        return MISSING
    if answered.was_answered(run):
        return ANSWERED
    gathered = getattr(run, "gathered_context", None) or {}
    disposition = str(gathered.get("mapped_call_disposition") or "").lower()
    tags = gathered.get("call_tags") or []
    completed = bool(getattr(run, "is_completed", False))
    if completed and (disposition in _CARRIER_FAILED):
        return CARRIER_FAILED
    if completed and (disposition in _NOT_CONNECTED or "not_connected" in tags):
        return NOT_CONNECTED
    return PENDING


async def read(workflow_run_id: int | None, organization_id: int) -> tuple[str, Any]:
    """``(evidence, run)`` for a run id in this workspace, read fresh. Never
    raises: a read that fails is ``PENDING`` (we do not know), not
    ``MISSING``. Scoped by ``organization_id``: a run id on a row is not
    proof the run is that workspace's."""
    from loguru import logger

    from api.db import db_client

    if not workflow_run_id:
        return MISSING, None
    try:
        run = await db_client.get_workflow_run(
            int(workflow_run_id), organization_id=organization_id
        )
    except Exception as exc:  # noqa: BLE001 - unknown, not absent
        logger.warning("call_evidence: could not read run {}: {}", workflow_run_id, exc)
        return PENDING, None
    return classify(run), run
