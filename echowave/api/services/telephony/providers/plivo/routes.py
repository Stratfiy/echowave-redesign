"""Plivo telephony routes (webhooks, status callbacks, answer URLs).

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the
provider registry — see ProviderSpec.router.
"""

import json

from xml.sax.saxutils import escape

from fastapi import APIRouter, Request
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
        return {"status": "error", "reason": "invalid_signature"}

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
async def handle_plivo_transfer_caller(conference_name: str):
    """XML for the **caller** leg, fetched when its live call is redirected.

    No ``<Speak>``: the caller has already been told they are being put
    through, and a second announcement here plays over the hold music of a
    bridge they are about to enter.

    ``startConferenceOnEnter`` is false so that a caller who arrives before the
    destination answers waits rather than opening an empty conference —
    Plivo would otherwise treat the first entrant as the start and the
    destination would join a room the caller had already been sitting alone in.
    """
    plivo_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Conference
        endConferenceOnExit="false"
        startConferenceOnEnter="false"
    >{escape(conference_name)}</Conference>
</Response>"""
    return Response(content=plivo_xml, media_type="application/xml")
