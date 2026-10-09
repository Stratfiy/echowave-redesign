"""Plivo telephony routes (webhooks, status callbacks, answer URLs).

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the
provider registry — see ProviderSpec.router.
"""

import json
from urllib.parse import quote
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pipecat.utils.run_context import set_current_run_id
from starlette.responses import HTMLResponse, Response

from api.db import db_client
from api.services.telephony.escalation import DEFAULT_BRIEFING
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)

router = APIRouter()


async def _handle_plivo_status_callback(
    workflow_run_id: int,
    request: Request,
):
    set_current_run_id(workflow_run_id)

    form_data = await request.form()
    callback_data = dict(form_data)
    logger.info(
        f"[run {workflow_run_id}] Received Plivo callback: {json.dumps(callback_data)}"
    )

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(f"Workflow run {workflow_run_id} not found for Plivo callback")
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning(f"Workflow {workflow_run.workflow_id} not found")
        return {"status": "ignored", "reason": "workflow_not_found"}

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )

    is_valid = await provider.verify_inbound_signature(
        str(request.url),
        callback_data,
        dict(request.headers),
    )
    if not is_valid:
        logger.warning(f"[run {workflow_run_id}] Invalid Plivo webhook signature")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    parsed_data = provider.parse_status_callback(callback_data)
    status_update = StatusCallbackRequest(
        call_id=parsed_data["call_id"],
        status=parsed_data["status"],
        from_number=parsed_data.get("from_number"),
        to_number=parsed_data.get("to_number"),
        direction=parsed_data.get("direction"),
        duration=parsed_data.get("duration"),
        extra=parsed_data.get("extra", {}),
    )

    await _process_status_update(workflow_run_id, status_update)
    return {"status": "success"}


@router.post("/plivo-xml", include_in_schema=False)
async def handle_plivo_xml_webhook(
    workflow_id: int,
    workflow_run_id: int,
    organization_id: int,
    request: Request,
):
    """
    Handle initial webhook from Plivo when an outbound call is answered.
    Returns Plivo XML response with Stream element.
    """
    set_current_run_id(workflow_run_id)
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    provider = await get_telephony_provider_for_run(workflow_run, organization_id)

    form_data = await request.form()
    callback_data = dict(form_data)

    is_valid = await provider.verify_inbound_signature(
        str(request.url), callback_data, dict(request.headers)
    )
    if not is_valid:
        logger.warning(
            f"[run {workflow_run_id}] Invalid Plivo signature on answer webhook"
        )
        return provider.generate_error_response(
            "invalid_signature", "Invalid webhook signature."
        )

    call_id = callback_data.get("CallUUID") or callback_data.get("RequestUUID")
    if call_id:
        gathered_context = dict(workflow_run.gathered_context or {})
        gathered_context["call_id"] = call_id
        await db_client.update_workflow_run(
            run_id=workflow_run_id, gathered_context=gathered_context
        )

    response_content = await provider.get_webhook_response(
        workflow_id, organization_id, workflow_run_id
    )
    return HTMLResponse(content=response_content, media_type="application/xml")


@router.post("/plivo/hangup-callback/{workflow_run_id}")
async def handle_plivo_hangup_callback(
    workflow_run_id: int,
    request: Request,
):
    """Handle Plivo hangup callbacks."""
    return await _handle_plivo_status_callback(workflow_run_id, request)


@router.post("/plivo/ring-callback/{workflow_run_id}")
async def handle_plivo_ring_callback(
    workflow_run_id: int,
    request: Request,
):
    """Handle Plivo ring callbacks."""
    return await _handle_plivo_status_callback(workflow_run_id, request)


# ---------------------------------------------------------------------------
# Call transfer
#
# Plivo has no equivalent of Twilio's inline ``Twiml`` parameter — every leg is
# driven by XML fetched from a URL. So the two legs of a warm transfer are two
# endpoints here rather than two strings in the provider, and the conference
# name travels in the path.
# ---------------------------------------------------------------------------


@router.api_route(
    "/plivo/transfer-bridge/{conference_name}",
    methods=["GET", "POST"],
    include_in_schema=False,
)
async def handle_plivo_transfer_bridge(conference_name: str, request: Request):
    """XML for the **destination** leg: brief the human, then join the bridge.

    ``<Speak>`` runs to completion before ``<Conference>`` is entered, and the
    caller is not in the conference while it plays, so the briefing is heard by
    the person picking up and by nobody else. That ordering is the entire warm-
    transfer mechanism — reverse the two elements and the caller hears you
    describing them.

    ``endConferenceOnExit`` is on this leg because the destination leaving is
    what ends the transfer; the caller's leg then falls out of a conference
    that no longer exists rather than sitting in an empty room.

    Plivo fetches this with the method configured on the call, and retries on a
    non-2xx, so it is a plain read of the path and cannot fail on bad input:
    an unknown conference name yields a valid, empty conference rather than a
    500 that would drop the leg.
    """
    briefing = request.query_params.get("briefing") or DEFAULT_BRIEFING

    plivo_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Speak>{escape(briefing)}</Speak>
    <Conference
        endConferenceOnExit="true"
        startConferenceOnEnter="true"
        waitSound=""
    >{escape(conference_name)}</Conference>
</Response>"""
    return Response(content=plivo_xml, media_type="application/xml")


@router.api_route(
    "/plivo/transfer-caller/{conference_name}",
    methods=["GET", "POST"],
    include_in_schema=False,
)
async def handle_plivo_transfer_caller(
    conference_name: str, escalation: str | None = None
):
    """XML for the **caller** leg, fetched when its live call is redirected.

    No ``<Speak>``: the caller has already been told they are being put
    through, and a second announcement here plays over the hold music of a
    bridge they are about to enter.

    ``startConferenceOnEnter`` is false so that a caller who arrives before the
    destination answers waits rather than opening an empty conference —
    Plivo would otherwise treat the first entrant as the start and the
    destination would join a room the caller had already been sitting alone in.

    ``escalation`` (escalation v2 only) adds a conference callback, so the
    caller's entry triggers the three-way introduction heard by both.
    """
    callback = ""
    if escalation:
        from api.utils.common import get_backend_endpoints

        backend_endpoint, _ = await get_backend_endpoints()
        url = (
            f"{backend_endpoint}/api/v1/telephony/plivo/escalation-intro/"
            f"{quote(escalation, safe='')}"
        )
        callback = (
            f'\n        callbackUrl="{escape(url)}"\n        callbackMethod="POST"'
        )
    plivo_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Conference
        endConferenceOnExit="false"
        startConferenceOnEnter="false"{callback}
    >{escape(conference_name)}</Conference>
</Response>"""
    return Response(content=plivo_xml, media_type="application/xml")


# ---------------------------------------------------------------------------
# Escalation v2 (services/escalation). Carrier callbacks for the person's leg
# and the caller's: whether a person or a machine answered, the three-way
# introduction once the caller is in the conference, and the stream the
# caller is handed back to. Each verifies Plivo's signature against the
# account of the run it belongs to before it acts.
# ---------------------------------------------------------------------------


@router.post("/plivo/escalation-amd/{transfer_id}", include_in_schema=False)
async def handle_plivo_escalation_amd(transfer_id: str, request: Request):
    """Plivo's answering-machine verdict on a person's leg.

    ``Machine=false`` is the only thing that tells the waiting call a person
    answered, so a voicemail greeting is never bridged; ``true`` drops the
    leg and the ladder rings the next person.
    """
    from api.services.escalation import actions as escalation_actions
    from api.services.escalation.dialer import report_leg_outcome
    from api.services.telephony.webhook_guard import (
        provider_for_transfer,
        require_signature,
    )

    data = dict(await request.form())
    resolved = await provider_for_transfer(transfer_id)
    if resolved is None:
        return {"status": "ignored", "reason": "unknown_transfer"}
    provider, _context = resolved
    await require_signature(request, provider, data)

    machine = str(data.get("Machine") or "").strip().lower() == "true"
    call_id = data.get("CallUUID") or data.get("RequestUUID")
    if machine:
        logger.info(f"[Plivo Escalation] {transfer_id}: a machine answered")
        await report_leg_outcome(
            transfer_id, human=False, reason="answered_by_machine", call_id=call_id
        )
        if call_id:
            await provider.hangup_transfer_leg(call_id)
        return {"status": "machine"}
    await escalation_actions.on_human_answered(transfer_id, call_id)
    await report_leg_outcome(transfer_id, human=True, call_id=call_id)
    return {"status": "human"}


@router.post("/plivo/escalation-intro/{escalation_uuid}", include_in_schema=False)
async def handle_plivo_escalation_intro(escalation_uuid: str, request: Request):
    """The caller's conference events: on their entry, one line to both."""
    from api.services.escalation import actions as escalation_actions
    from api.services.telephony.call_transfer_manager import (
        get_call_transfer_manager,
    )
    from api.services.telephony.webhook_guard import (
        provider_for_run_id,
        require_signature,
    )

    data = dict(await request.form())
    row = await db_client.get_escalation_by_uuid_unscoped(escalation_uuid)
    if row is None or not row.workflow_run_id:
        return {"status": "ignored"}
    resolved = await provider_for_run_id(int(row.workflow_run_id))
    if resolved is None:
        return {"status": "ignored"}
    provider, _run = resolved
    await require_signature(request, provider, data)
    if str(data.get("ConferenceAction") or "").lower() != "enter":
        return {"status": "ignored"}
    manager = await get_call_transfer_manager()
    redis = await manager._get_redis()
    if not await redis.set(
        f"escalation:intro:{escalation_uuid}", "1", nx=True, ex=3600
    ):
        return {"status": "already_said"}
    name = None
    for attempt in row.attempts or []:
        if attempt.get("transfer_id") == row.current_transfer_id:
            name = str(attempt.get("target") or "").rsplit("…", 1)[0].strip() or None
    said = await provider.speak_into_conference(
        str(data.get("ConferenceName") or ""),
        escalation_actions.intro_line(row.handoff_card or {}, name),
    )
    return {"status": "said" if said else "not_said"}


@router.api_route(
    "/plivo/escalation-handback/{token}",
    methods=["GET", "POST"],
    include_in_schema=False,
)
async def handle_plivo_escalation_handback(token: str, request: Request):
    """XML for a caller handed back to the agent: a fresh agent stream."""
    from api.services.escalation import actions as escalation_actions
    from api.services.escalation.handback import start_resumed_run
    from api.services.telephony.webhook_guard import (
        provider_for_run_id,
        require_signature,
    )

    data = dict(await request.form()) if request.method == "POST" else {}
    pending = await escalation_actions.peek_handback(token)
    if not pending or not pending.get("workflow_run_id"):
        return _hangup_xml("Sorry, this call could not be continued.")
    resolved = await provider_for_run_id(int(pending["workflow_run_id"]))
    if resolved is None:
        return _hangup_xml("Sorry, this call could not be continued.")
    provider, original_run = resolved
    await require_signature(request, provider, data)
    claim = await escalation_actions.claim_handback(token)
    if claim is None:
        return _hangup_xml("Sorry, this call could not be continued.")
    try:
        return await start_resumed_run(
            claim,
            provider=provider,
            original_run=original_run,
            call_id=data.get("CallUUID"),
        )
    except Exception as exc:  # noqa: BLE001 - say something rather than drop
        logger.error(f"[Plivo Escalation] Hand back could not start: {exc}")
        return _hangup_xml(
            "Sorry, I couldn't come back on the line. The team will call you back."
        )


def _hangup_xml(text: str) -> Response:
    return Response(
        content=f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Speak>{escape(text)}</Speak>
    <Hangup/>
</Response>""",
        media_type="application/xml",
    )
