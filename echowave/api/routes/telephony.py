"""
Telephony routes - handles all telephony-related endpoints.
Consolidated from split modules for easier maintenance.
"""

import json
import uuid
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    WebSocket,
)
from loguru import logger
from pipecat.utils.run_context import set_current_run_id
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

from api.constants import (
    SHARED_OUTBOUND_MAX_CONCURRENT,
    TELEPHONY_WS_REQUIRE_TOKEN,
)
from api.db import db_client
from api.db.models import UserModel
from api.db.workflow_run_client import WorkflowRunStateConflictError
from api.enums import CallType, WorkflowRunMode, WorkflowRunState
from api.errors.telephony_errors import TelephonyError
from api.sdk_expose import sdk_expose
from api.services.auth.depends import get_user
from api.services.call_concurrency import (
    CallConcurrencyLimitError,
    WorkflowRunSlotAlreadyBoundError,
    call_concurrency,
)
from api.services.compliance import dnd
from api.services.configuration import key_readiness
from api.services.kyc import service as kyc_service
from api.services.quota_service import authorize_workflow_run_start
from api.services.telephony import (
    inbound_guard,
    missed_call,
    number_lifecycle,
    shared_outbound,
    stream_capability,
    verified_numbers,
    voice_otp,
)
from api.services.telephony.call_transfer_manager import get_call_transfer_manager
from api.services.telephony.escalation import destination_is_human
from api.services.telephony.factory import (
    get_all_telephony_providers,
    get_default_telephony_provider,
    get_telephony_provider_by_id,
    get_telephony_provider_for_run,
)
from api.services.telephony.shared_outbound import SHARED_OUTBOUND_SCOPE
from api.services.telephony.transfer_event_protocol import (
    TransferEvent,
    TransferEventType,
)
from api.services.workflow import liveness
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames
from api.utils.common import get_backend_endpoints
from api.utils.telephony_helper import (
    generic_hangup_response,
    normalize_webhook_data,
    numbers_match,
    parse_webhook_request,
)

router = APIRouter(prefix="/telephony")


class InitiateCallRequest(BaseModel):
    workflow_id: int
    workflow_run_id: int | None = None
    phone_number: str | None = None
    # Optional explicit telephony config to use for the test call. If omitted,
    # falls back to the org default.
    telephony_configuration_id: int | None = None
    # Optional caller-ID phone number to dial out from. Must belong to the
    # resolved telephony configuration; otherwise the provider picks one.
    from_phone_number_id: int | None = None


def _get_execution_user_id(workflow) -> int:
    if workflow.user_id is None:
        raise HTTPException(
            status_code=409,
            detail="Workflow has no execution owner",
        )
    return workflow.user_id


@router.post(
    "/initiate-call",
    **sdk_expose(
        method="test_phone_call",
        description="Place a test call from a workflow to a phone number.",
    ),
)
async def initiate_call(
    request: InitiateCallRequest, user: UserModel = Depends(get_user)
):
    """Initiate a call using the configured telephony provider from web browser. This is
    supposed to be a test call method for the draft version of the agent."""

    from api.services.organization_preferences import get_organization_preferences

    preferences = await get_organization_preferences(
        user.selected_organization_id,
        db=db_client,
    )

    # Resolve which telephony config to use: explicit request value, otherwise
    # the org's default outbound config, otherwise Decibyl's shared pool.
    telephony_configuration_id = request.telephony_configuration_id
    using_shared_caller_id = False

    if telephony_configuration_id:
        try:
            provider = await get_telephony_provider_by_id(
                telephony_configuration_id, user.selected_organization_id
            )
        except ValueError:
            raise HTTPException(
                status_code=400, detail="telephony_configuration_not_found"
            )
    else:
        try:
            provider = await get_default_telephony_provider(
                user.selected_organization_id
            )
            default_cfg = await db_client.get_default_telephony_configuration(
                user.selected_organization_id
            )
            telephony_configuration_id = default_cfg.id if default_cfg else None
        except ValueError:
            # No carrier account of their own. Rather than the dead end this
            # used to be — `telephony_not_configured`, which is true and
            # useless to someone evaluating the product — fall back to
            # Decibyl's own shared caller IDs.
            #
            # Gated on the destination being a number this account has proved
            # it holds, and that gate is not optional here even when it is off
            # for accounts dialling on their own carrier: on their trunk an
            # unverified number is their problem, on ours it is us ringing a
            # stranger for free.
            try:
                (
                    telephony_configuration_id,
                    provider,
                    _shared_numbers,
                ) = await shared_outbound.get_provider()
            except shared_outbound.NoSharedNumber:
                raise HTTPException(status_code=400, detail="telephony_not_configured")
            using_shared_caller_id = True

    # Validate provider is configured
    if not provider.validate_config():
        raise HTTPException(
            status_code=400,
            detail="telephony_not_configured",
        )

    # A number on our own carrier account may only be used once the licensee
    # has verified this customer. Checked before a concurrency slot is taken,
    # so a blocked account gets a sentence it can act on rather than a call
    # that dies at the trunk. Bring-your-own configurations pass through.
    if telephony_configuration_id is not None:
        configuration = await db_client.get_telephony_configuration_for_org(
            telephony_configuration_id, user.selected_organization_id
        )
        try:
            await kyc_service.assert_configuration_may_place_calls(configuration)
        except kyc_service.TelephonyNotVerified as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        # Verified is not the same as paid for. A number whose rental is
        # overdue past the suspension threshold stops carrying calls while
        # still being held at the carrier, so this is a separate check from
        # the KYC one above and fails with a different sentence.
        try:
            await number_lifecycle.assert_configuration_may_serve(
                telephony_configuration_id
            )
        except number_lifecycle.NumberSuspended as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc

    phone_number = request.phone_number or preferences.test_phone_number

    if not phone_number:
        raise HTTPException(
            status_code=400,
            detail="Phone number must be provided in request or set in organization preferences",
        )

    # A test call may only go to a number this account has proved it can
    # answer. Without it, test_phone_number is free text that we dial — an
    # account can have Decibyl ring any number its user types, which is
    # telephone harassment wearing the costume of a convenience feature.
    #
    # Scoped to the test-call path deliberately. Campaigns and the trigger API
    # dial numbers the customer supplies as data; those are governed by the DND
    # list and the calling window below, not by ownership proof, because a
    # customer does not own their contact list's phone numbers.
    # `using_shared_caller_id` forces the check on regardless of the global
    # default. The flag is off platform-wide because `VERIFICATION_CHANNEL` is
    # `log` on every real deployment, so nobody could obtain the permission —
    # but that argument is about an account dialling on *its own* trunk, where
    # an unverified destination is its own affair. On ours it is Decibyl ringing
    # a stranger, for free, from a number that identifies us.
    # Resolved once, because two decisions turn on it: whether this account may
    # dial the number at all, and whether the calling window applies to it.
    destination_is_verified = await verified_numbers.is_verified(
        user.selected_organization_id,
        phone_number,
        # This route's own client, for the reason spelled out at the DND gate
        # below: is_verified would otherwise import its own, which is a second
        # engine on a second event loop.
        db=db_client,
    )
    from api.services.telephony import verification_sender

    if (
        verification_sender.test_calls_require_verified_number()
        or using_shared_caller_id
    ) and not destination_is_verified:
        # Telling someone to verify a number on a deployment that cannot send a
        # code is a loop: they follow the instruction, the verify screen answers
        # 502, and nothing on either screen says the step is impossible here.
        # `REQUIRE_VERIFIED_TEST_NUMBER` is off by default for exactly this
        # reason — "a permission nobody can obtain is not a permission, it is an
        # outage" — but `using_shared_caller_id` turns the same gate on for the
        # accounts most likely to meet it: the ones with no carrier of their
        # own, on their first call. So say which of the two situations this is.
        if verification_sender.is_deliverable():
            detail = (
                "Verify this number before calling it. Add it under Verified "
                "numbers and enter the code we send you."
            )
        elif using_shared_caller_id:
            detail = (
                "Test calls on Decibyl's shared caller ID only go to a verified "
                "number, and this deployment cannot send verification codes yet. "
                "Connect your own telephony provider under Telephony to place "
                "this call, or ask your administrator to configure "
                "VERIFICATION_CHANNEL."
            )
        else:
            detail = (
                "This number is not verified, and this deployment cannot send "
                "verification codes. Ask your administrator to configure "
                "VERIFICATION_CHANNEL, or to turn REQUIRE_VERIFIED_TEST_NUMBER "
                "off until it is."
            )
        raise HTTPException(status_code=403, detail=detail)

    if using_shared_caller_id:
        logger.info(
            f"Org {user.selected_organization_id} has no carrier account; "
            f"placing this test call on Decibyl's shared caller ID pool "
            f"(configuration {telephony_configuration_id})."
        )

    # TCCCPR: the do-not-disturb list and the calling window. Checked here, at
    # the last point before the call is placed, rather than anywhere earlier —
    # the window is a fact about now, and "now" moves between queueing a call
    # and dialling it.
    #
    # The window does not apply to a number this account has verified. Those
    # hours exist so a stranger is not rung at night; a developer testing their
    # own agent on their own handset at 21:54 is not a stranger, and refusing
    # that call protects nobody while making the product untestable for half
    # the working day. Ownership is proved, not asserted — `is_verified` means
    # a code was sent to that handset and typed back. Campaigns and the trigger
    # API dial numbers supplied as data and keep the full window; this is the
    # one path where the destination belongs to the caller.
    #
    # 451 rather than 403: the call is refused for a legal reason, not because
    # this user lacks permission. The distinction matters to whoever reads the
    # log, because the two have completely different remedies.
    try:
        phone_number = await dnd.assert_may_call(
            user.selected_organization_id,
            phone_number,
            timezone_name=preferences.timezone,
            enforce_calling_hours=not destination_is_verified,
            # The module-level client this route uses for everything else.
            # assert_may_call would otherwise import its own, which is a second
            # engine on a second event loop — harmless in production and a
            # false 451 under test, where the refusal is indistinguishable from
            # a real DND hit.
            db=db_client,
        )
    except dnd.CallRefused as exc:
        raise HTTPException(status_code=451, detail=str(exc)) from exc

    workflow = await db_client.get_workflow(
        request.workflow_id, organization_id=user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # 409 rather than 404 or 403: the agent exists and the caller may use it,
    # but it is switched off. That is a state the caller can change, and the
    # distinction is what makes the message actionable.
    try:
        liveness.assert_workflow_may_take_calls(workflow)
    except liveness.AgentNotTakingCalls as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Same 409, same reasoning: a slot set to the account's own key with no
    # usable key behind it is a state the caller can fix. Refused here rather
    # than at pipeline start, where it would already have cost a real call that
    # answered with silence.
    try:
        await key_readiness.assert_workflow_may_run(workflow)
    except key_readiness.ProviderKeyMissing as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    execution_user_id = _get_execution_user_id(workflow)

    # Determine the workflow run mode based on provider type
    workflow_run_mode = provider.PROVIDER_NAME

    # Resolve optional caller-ID. The config has already been validated against
    # the user's organization, so filtering by config_id is sufficient for
    # tenant isolation.
    from_number: str | None = None
    if request.from_phone_number_id is not None:
        if telephony_configuration_id is None:
            raise HTTPException(
                status_code=400,
                detail="from_phone_number_id_requires_telephony_configuration",
            )
        phone_row = await db_client.get_phone_number_for_config(
            request.from_phone_number_id, telephony_configuration_id
        )
        if not phone_row or not phone_row.is_active:
            raise HTTPException(status_code=400, detail="from_phone_number_not_found")
        from_number = phone_row.address_normalized

    workflow_run_id = request.workflow_run_id
    try:
        # A trial call on our shared caller IDs is additionally counted against
        # one platform-wide total. The per-organization limit does not bound
        # that pool at all: it counts each account separately, so ten
        # evaluators dialling at once is ten accounts each comfortably inside
        # their own limit and one carrier account -- ours -- carrying every
        # minute of it.
        #
        # The scope counter is keyed on the scope alone rather than on the
        # organization (see rate_limiter.try_acquire_concurrent_slot_details),
        # which is what makes it a platform total, and it is taken in the same
        # atomic script as the org slot so the two cannot disagree.
        concurrency_slot = await call_concurrency.acquire_org_slot(
            user.selected_organization_id,
            source="telephony_outbound",
            timeout=0,
            scope_key=SHARED_OUTBOUND_SCOPE if using_shared_caller_id else None,
            scope_max_concurrent=(
                SHARED_OUTBOUND_MAX_CONCURRENT if using_shared_caller_id else None
            ),
        )
    except CallConcurrencyLimitError:
        if using_shared_caller_id:
            # Distinguished from the org limit because the remedies are
            # opposite: this one is not about anything the account did, and
            # waiting is the whole of the fix.
            raise HTTPException(
                status_code=429,
                detail=(
                    "Every shared test line is busy right now. Try again in a "
                    "moment, or connect your own carrier to dial without "
                    "waiting."
                ),
            )
        raise HTTPException(status_code=429, detail="Concurrent call limit reached")

    try:
        if not workflow_run_id:
            # Merge template context variables (e.g. caller_number, called_number
            # set in workflow settings for testing pre-call data fetch).
            template_vars = workflow.template_context_variables or {}

            numeric_suffix = int(str(uuid.uuid4()).replace("-", "")[:8], 16) % 100000000
            workflow_run_name = f"WR-TEL-OUT-{numeric_suffix:08d}"
            workflow_run = await db_client.create_workflow_run(
                workflow_run_name,
                workflow.id,
                workflow_run_mode,
                user_id=execution_user_id,
                call_type=CallType.OUTBOUND,
                initial_context={
                    **template_vars,
                    "phone_number": phone_number,
                    "called_number": phone_number,
                    "provider": provider.PROVIDER_NAME,
                    "telephony_configuration_id": telephony_configuration_id,
                },
                use_draft=True,
                organization_id=user.selected_organization_id,
            )
            workflow_run_id = workflow_run.id
        else:
            workflow_run = await db_client.get_workflow_run(
                workflow_run_id, organization_id=user.selected_organization_id
            )
            if not workflow_run:
                raise HTTPException(status_code=400, detail="Workflow run not found")
            if workflow_run.workflow_id != workflow.id:
                raise HTTPException(
                    status_code=400,
                    detail="workflow_run_workflow_mismatch",
                )
            workflow_run_name = workflow_run.name

        await call_concurrency.bind_workflow_run(concurrency_slot, workflow_run_id)
    except WorkflowRunSlotAlreadyBoundError:
        raise HTTPException(
            status_code=409,
            detail="Workflow run already has an active call",
        )
    except Exception:
        await call_concurrency.release_slot(concurrency_slot)
        raise

    # Check Decibyl quota after the run exists so hosted v2 can mint and store
    # the MPS correlation id before initiating the call.
    quota_result = await authorize_workflow_run_start(
        workflow_id=workflow.id,
        organization_id=user.selected_organization_id,
        workflow_run_id=workflow_run_id,
        actor_user=user,
    )
    if not quota_result.has_quota:
        await call_concurrency.release_workflow_run_slot(workflow_run_id)
        raise HTTPException(status_code=402, detail=quota_result.error_message)

    try:
        # Construct webhook URL based on provider type
        backend_endpoint, _ = await get_backend_endpoints()

        webhook_endpoint = provider.WEBHOOK_ENDPOINT

        webhook_url = (
            f"{backend_endpoint}/api/v1/telephony/{webhook_endpoint}"
            f"?workflow_id={workflow.id}"
            f"&workflow_run_id={workflow_run_id}"
            f"&organization_id={user.selected_organization_id}"
        )

        keywords = {
            "workflow_id": workflow.id,
            "organization_id": user.selected_organization_id,
        }

        # Initiate call via provider
        result = await provider.initiate_call(
            to_number=phone_number,
            webhook_url=webhook_url,
            workflow_run_id=workflow_run_id,
            from_number=from_number,
            **keywords,
        )
    except Exception:
        await call_concurrency.release_workflow_run_slot(workflow_run_id)
        raise

    # Store provider metadata and caller_number in workflow run context
    gathered_context = {
        "provider": provider.PROVIDER_NAME,
        **(result.provider_metadata or {}),
    }
    # Merge caller_number into initial_context now that we know which number was used
    updated_initial_context = {
        **(workflow_run.initial_context or {}),
        "called_number": phone_number,
        "telephony_configuration_id": telephony_configuration_id,
    }
    if result.caller_number:
        updated_initial_context["caller_number"] = result.caller_number
    await db_client.update_workflow_run(
        run_id=workflow_run_id,
        gathered_context=gathered_context,
        initial_context=updated_initial_context,
    )

    return {"message": f"Call initiated successfully with run name {workflow_run_name}"}


async def _verify_organization_phone_number(
    phone_number: str,
    organization_id: int,
    telephony_configuration_id: int,
    provider: str,
    to_country: str = None,
    from_country: str = None,
) -> Optional[int]:
    """Verify the called number is registered to the matched config and return
    its ``telephony_phone_numbers.id``, or None when no row matches.

    Primary path: deterministic E.164 / SIP lookup via the new phone-number table.
    Legacy fallback: ``numbers_match()`` over the matched config's active numbers,
    so non-E.164 rows that survived the migration still route correctly.
    """
    try:
        match = await db_client.find_active_phone_number_for_inbound(
            organization_id, phone_number, provider, country_hint=to_country
        )
        if match and match.telephony_configuration_id == telephony_configuration_id:
            logger.info(
                f"Phone number {phone_number} matched row {match.id} for org "
                f"{organization_id} / config {telephony_configuration_id}"
            )
            return match.id

        # Legacy fallback: scan the matched config's active numbers and apply
        # the country-aware fuzzy matcher (covers non-E.164 storage).
        rows = await db_client.list_phone_numbers_for_config(telephony_configuration_id)
        for row in rows:
            if not row.is_active:
                continue
            if numbers_match(phone_number, row.address, to_country, from_country):
                logger.info(
                    f"Phone number {phone_number} matched (fuzzy) row {row.id} "
                    f"for config {telephony_configuration_id}"
                )
                return row.id

        logger.warning(
            f"Phone number {phone_number} not registered to config "
            f"{telephony_configuration_id} (org={organization_id}, "
            f"to_country={to_country}, from_country={from_country})"
        )
        return None

    except Exception as e:
        logger.error(
            f"Error verifying phone number {phone_number} for organization "
            f"{organization_id} / config {telephony_configuration_id}: {e}"
        )
        return None


async def _record_missed_call(organization_id: int, phone_row, normalized_data) -> None:
    """Log the ring and hand the callback to a worker.

    Never raises. The carrier is holding the webhook open for a hangup, and a
    500 here makes it retry a request whose only job was to decline — so a
    failure to enqueue must cost us the callback, not the hangup.

    The row is written before the job is enqueued so that a worker that never
    picks it up leaves a `pending` row rather than no trace at all. That row is
    the difference between "nobody rang the hoarding" and "we dropped every
    caller", which look identical on an empty dashboard.
    """
    caller = dnd.normalise_number(normalized_data.from_number)
    if not caller:
        logger.warning(
            f"/inbound/run: callback-mode number {normalized_data.to_number} "
            f"rang by an unusable caller id {normalized_data.from_number!r}; "
            "nothing to call back"
        )
        return

    try:
        event = await db_client.record_missed_call(
            organization_id=organization_id,
            telephony_phone_number_id=phone_row.id,
            caller=caller,
            provider=normalized_data.provider,
        )
        await enqueue_job(
            FunctionNames.PLACE_MISSED_CALL_CALLBACK, event.id, organization_id
        )
        logger.info(
            f"/inbound/run: missed call from {caller} on {normalized_data.to_number}; "
            f"callback queued as event {event.id}"
        )
    except Exception as exc:  # noqa: BLE001 -- see docstring
        logger.error(f"Could not queue missed-call callback for {caller}: {exc}")


async def _detect_provider(webhook_data: dict, headers: dict):
    """Detect which telephony provider can handle this webhook"""
    provider_classes = await get_all_telephony_providers()

    for provider_class in provider_classes:
        if provider_class.can_handle_webhook(webhook_data, headers):
            return provider_class

    logger.warning(f"No provider found for webhook data: {webhook_data.keys()}")
    return None


async def _validate_inbound_request(
    workflow_id: int,
    webhook_url: str,
    provider_class,
    normalized_data,
    webhook_data: dict,
    headers: dict,
    raw_body: str = "",
) -> tuple[bool, TelephonyError, dict, object]:
    """
    Validate all aspects of inbound request.
    Returns: (is_valid, error_type, workflow_context, provider_instance)
    """
    from api.services.telephony import registry as telephony_registry

    # System lookup: inbound routing only has the workflow_id and derives the
    # org/user from the workflow itself, so use the explicit unscoped variant.
    workflow = await db_client.get_workflow_by_id(workflow_id)
    if not workflow:
        return False, TelephonyError.WORKFLOW_NOT_FOUND, {}, None

    organization_id = workflow.organization_id
    user_id = workflow.user_id
    provider = normalized_data.provider

    # Primary path: one combined query that resolves config + phone number
    # together (joins configs and phone_numbers with provider, account_id,
    # and called-number filters). Falls back to the two-step config-then-
    # phone resolution to cover providers without account_id (ARI) and
    # legacy non-E.164 stored addresses.
    spec = telephony_registry.get_optional(provider_class.PROVIDER_NAME)
    account_field = spec.account_id_credential_field if spec else ""

    telephony_configuration_id: Optional[int] = None
    phone_number_id: Optional[int] = None

    if account_field and normalized_data.account_id:
        match = await db_client.find_inbound_route_by_account(
            provider=provider_class.PROVIDER_NAME,
            account_id_field=account_field,
            account_id=normalized_data.account_id,
            to_number=normalized_data.to_number,
            country_hint=normalized_data.to_country,
            organization_id=organization_id,
        )
        if match:
            cfg_row, phone_row = match
            telephony_configuration_id = cfg_row.id
            phone_number_id = phone_row.id

    if telephony_configuration_id is None:
        (
            validation_result,
            telephony_configuration_id,
        ) = await _resolve_inbound_telephony_config(
            organization_id, provider_class, normalized_data.account_id
        )
        if validation_result != TelephonyError.VALID:
            return False, validation_result, {}, None

        phone_number_id = await _verify_organization_phone_number(
            normalized_data.to_number,
            organization_id,
            telephony_configuration_id,
            provider_class.PROVIDER_NAME,
            normalized_data.to_country,
            normalized_data.from_country,
        )
        if phone_number_id is None:
            return False, TelephonyError.PHONE_NUMBER_NOT_CONFIGURED, {}, None

    # Verify webhook signature using the matched config's credentials. The
    # provider extracts its own signature/timestamp/nonce headers from the
    # dict, so this dispatcher stays generic.
    provider_instance = await get_telephony_provider_by_id(
        telephony_configuration_id, organization_id
    )
    signature_valid = await provider_instance.verify_inbound_signature(
        webhook_url, webhook_data, headers, raw_body
    )
    logger.info(f"Signature validation for {provider}: {signature_valid}")
    if not signature_valid:
        return (
            False,
            TelephonyError.SIGNATURE_VALIDATION_FAILED,
            {},
            provider_instance,
        )

    # Return success with workflow context
    workflow_context = {
        "workflow": workflow,
        "organization_id": organization_id,
        "user_id": user_id,
        "provider": provider,
        "telephony_configuration_id": telephony_configuration_id,
        "from_phone_number_id": phone_number_id,
    }
    return (True, "", workflow_context, provider_instance)


async def _create_inbound_workflow_run(
    workflow_id: int,
    user_id: int,
    organization_id: int,
    provider: str,
    normalized_data,
    telephony_configuration_id: int,
    from_phone_number_id: Optional[int] = None,
    contact_context: Optional[dict] = None,
) -> int:
    """Create workflow run for inbound call and return run ID"""
    call_id = normalized_data.call_id
    numeric_suffix = int(str(uuid.uuid4()).replace("-", "")[:8], 16) % 100000000
    workflow_run_name = f"WR-TEL-IN-{numeric_suffix:08d}"

    workflow_run = await db_client.create_workflow_run(
        workflow_run_name,
        workflow_id,
        provider,  # Use detected provider as mode
        user_id=user_id,
        call_type=CallType.INBOUND,
        initial_context={
            # What the number's contact list knows about this caller, if it
            # recognised them. First so the routing facts below cannot be
            # overwritten by a column somebody happened to name "provider" in
            # their spreadsheet.
            **(contact_context or {}),
            "caller_number": normalized_data.from_number,
            "called_number": normalized_data.to_number,
            "direction": "inbound",
            "provider": provider,
            "telephony_configuration_id": telephony_configuration_id,
        },
        gathered_context={
            "call_id": call_id,
        },
        logs={
            "inbound_webhook": {
                "account_id": normalized_data.account_id,
                "from_country": normalized_data.from_country,
                "to_country": normalized_data.to_country,
                "from_phone_number_id": from_phone_number_id,
                "raw_webhook_data": normalized_data.raw_data,
            },
        },
        organization_id=organization_id,
    )

    logger.info(
        f"Created inbound workflow run {workflow_run.id} for {provider} call {call_id}"
    )
    return workflow_run.id


async def _resolve_inbound_telephony_config(
    organization_id: int, provider_class, account_id: str
) -> tuple[TelephonyError, Optional[int]]:
    """Find which of the org's telephony configs the inbound webhook came from.

    Returns ``(VALID, config_id)`` on success or ``(error, None)`` otherwise.
    Replaces the single-config check that assumed one provider per org.
    """
    from api.services.telephony.factory import find_telephony_config_for_inbound

    try:
        candidates = await db_client.list_telephony_configurations_by_provider(
            organization_id, provider_class.PROVIDER_NAME
        )
        if not candidates:
            logger.warning(
                f"No {provider_class.PROVIDER_NAME} configuration for org "
                f"{organization_id}"
            )
            return TelephonyError.PROVIDER_MISMATCH, None

        match = await find_telephony_config_for_inbound(
            organization_id, provider_class.PROVIDER_NAME, account_id
        )
        if not match:
            logger.warning(
                f"Account validation failed for {provider_class.PROVIDER_NAME}: "
                f"webhook account_id={account_id} (org {organization_id})"
            )
            return TelephonyError.ACCOUNT_VALIDATION_FAILED, None

        config_id, _ = match
        return TelephonyError.VALID, config_id

    except Exception as e:
        logger.error(f"Exception during account validation: {e}")
        return TelephonyError.ACCOUNT_VALIDATION_FAILED, None


@router.websocket("/ws/ari")
async def websocket_ari_endpoint(websocket: WebSocket):
    """WebSocket endpoint for ARI chan_websocket external media.

    Asterisk connects here via chan_websocket. Routing params are passed as
    query params (appended by the v() dial string option in externalMedia).
    """
    workflow_id = websocket.query_params.get("workflow_id")
    organization_id = websocket.query_params.get("organization_id")
    workflow_run_id = websocket.query_params.get("workflow_run_id")

    if not workflow_id or not organization_id or not workflow_run_id:
        logger.error(
            f"ARI WebSocket missing query params: workflow_id={workflow_id}, "
            f"organization_id={organization_id}, workflow_run_id={workflow_run_id}"
        )
        await websocket.close(code=4400, reason="Missing required query params")
        return

    # Accept with "media" subprotocol — chan_websocket sends
    # Sec-WebSocket-Protocol: media and requires it echoed back.
    await websocket.accept(subprotocol="media")

    await _handle_telephony_websocket(
        websocket, int(workflow_id), int(organization_id), int(workflow_run_id)
    )


@router.websocket("/ws/{workflow_id}/{organization_id}/{workflow_run_id}")
async def websocket_endpoint(
    websocket: WebSocket, workflow_id: int, organization_id: int, workflow_run_id: int
):
    """WebSocket endpoint for real-time call handling - routes to provider-specific handlers."""
    # Checked before the handshake is accepted, so an unauthorised connection is
    # refused rather than accepted-then-closed. Accepting first would mean every
    # probe got a live socket for as long as it took us to say no.
    granted = await stream_capability.verify(
        websocket.query_params.get(stream_capability.TOKEN_PARAM),
        workflow_id=workflow_id,
        organization_id=organization_id,
        workflow_run_id=workflow_run_id,
    )
    if not granted:
        if TELEPHONY_WS_REQUIRE_TOKEN:
            logger.warning(
                f"Refusing media socket for run {workflow_run_id} (org "
                f"{organization_id}): no valid stream capability presented."
            )
            await websocket.close(code=4401, reason="Unauthorized")
            return
        # The escape hatch is on. Say so on every connection: this is an
        # unauthenticated socket, and it should be noisy for exactly as long as
        # somebody leaves it that way.
        logger.warning(
            f"Media socket for run {workflow_run_id} presented no valid stream "
            "capability and TELEPHONY_WS_REQUIRE_TOKEN is off — allowing it."
        )

    await websocket.accept()
    await _handle_telephony_websocket(
        websocket, workflow_id, organization_id, workflow_run_id
    )


async def _handle_telephony_websocket(
    websocket: WebSocket, workflow_id: int, organization_id: int, workflow_run_id: int
):
    """Shared WebSocket handler logic (connection already accepted).

    The caller has already established that whoever is connecting holds a
    capability minted for this exact triple — see
    ``services/telephony/stream_capability``. The org-scoped lookups below stay
    as they are: they are what turns a mismatched id into a 4404 rather than
    another tenant's call, and defence in depth is the right amount of defence
    for live audio.
    """
    try:
        # Set the run context
        set_current_run_id(workflow_run_id)

        # Get workflow run to determine provider type
        workflow_run = await db_client.get_workflow_run(
            workflow_run_id, organization_id=organization_id
        )
        if not workflow_run:
            logger.error(
                f"Workflow run {workflow_run_id} not found for org {organization_id}"
            )
            await websocket.close(code=4404, reason="Workflow run not found")
            return

        workflow = await db_client.get_workflow(
            workflow_id, organization_id=organization_id
        )
        if not workflow:
            logger.error(f"Workflow {workflow_id} not found for org {organization_id}")
            await websocket.close(code=4404, reason="Workflow not found")
            return
        if workflow_run.workflow_id != workflow.id:
            logger.error(
                f"Workflow run {workflow_run_id} belongs to workflow "
                f"{workflow_run.workflow_id}, not {workflow.id}"
            )
            await websocket.close(code=4400, reason="workflow_run_workflow_mismatch")
            return

        # Check workflow run state - only allow 'initialized' state
        if workflow_run.state != WorkflowRunState.INITIALIZED.value:
            logger.warning(
                f"Workflow run {workflow_run_id} not in initialized state: {workflow_run.state}"
            )
            await websocket.close(
                code=4409, reason="Workflow run not available for connection"
            )
            return

        # Extract provider type from workflow run context
        provider_type = None
        logger.info(
            f"Workflow run {workflow_run_id} gathered_context: {workflow_run.gathered_context}"
        )
        logger.info(f"Workflow run {workflow_run_id} mode: {workflow_run.mode}")

        if workflow_run.initial_context:
            provider_type = workflow_run.initial_context.get("provider")
            logger.info(f"Extracted provider_type: {provider_type}")

        if (
            workflow_run.mode == WorkflowRunMode.SMALLWEBRTC.value
            or provider_type == WorkflowRunMode.SMALLWEBRTC.value
        ):
            logger.warning(
                f"SmallWebRTC workflow run {workflow_run_id} reached telephony "
                f"websocket; mode={workflow_run.mode}, provider={provider_type}"
            )
            await websocket.close(
                code=4400,
                reason=(
                    "smallwebrtc runs connect through the WebRTC signaling endpoint, "
                    "not the telephony websocket"
                ),
            )
            return

        if not provider_type:
            logger.error(
                f"No provider type found in workflow run {workflow_run_id}. "
                f"gathered_context: {workflow_run.gathered_context}, mode: {workflow_run.mode}"
            )
            await websocket.close(
                code=4400,
                reason=(
                    f"No provider type found for workflow run {workflow_run_id} "
                    f"(mode: {workflow_run.mode}); telephony websocket requires "
                    "a telephony provider"
                ),
            )
            return

        logger.info(
            f"WebSocket connected for {provider_type} provider, workflow_run {workflow_run_id}"
        )

        provider = await get_telephony_provider_for_run(
            workflow_run, workflow.organization_id
        )

        # Verify the provider matches what was stored
        if provider.PROVIDER_NAME != provider_type:
            logger.error(
                f"Provider mismatch: expected {provider_type}, got {provider.PROVIDER_NAME}"
            )
            await websocket.close(code=4400, reason="Provider mismatch")
            return

        # Set workflow run state to 'running' before starting the pipeline.
        # ``expected_state`` makes this the authoritative check: the plain
        # read above can go stale across the awaits since (e.g. a terminal
        # status callback landing in that window), so the transition is only
        # safe once it's verified atomically under the row lock at write time.
        try:
            await db_client.update_workflow_run(
                run_id=workflow_run_id,
                state=WorkflowRunState.RUNNING.value,
                expected_state=WorkflowRunState.INITIALIZED.value,
            )
        except WorkflowRunStateConflictError as e:
            logger.warning(
                f"Workflow run {workflow_run_id} not in initialized state at "
                f"transition time: {e.actual_state}"
            )
            await websocket.close(
                code=4409, reason="Workflow run not available for connection"
            )
            return

        logger.info(
            f"[run {workflow_run_id}] Set workflow run state to 'running' for {provider_type} provider"
        )

        # Delegate to provider-specific handler
        await provider.handle_websocket(
            websocket, workflow_id, organization_id, workflow_run_id
        )

    except WebSocketDisconnect as e:
        logger.info(f"WebSocket disconnected: code={e.code}, reason={e.reason}")
    except Exception as e:
        logger.error(f"Error in WebSocket connection: {e}")
        try:
            await websocket.close(1011, "Internal server error")
        except RuntimeError:
            # WebSocket already closed, ignore
            pass


@router.post("/inbound/run")
async def handle_inbound_run(request: Request):
    """Workflow-agnostic inbound dispatcher.

    All providers can point a single webhook at this endpoint instead of one
    URL per workflow. The dispatcher resolves the org from the webhook's
    account_id and the workflow from the called number's
    ``inbound_workflow_id``. This is what ``configure_inbound`` writes into
    each provider's resource so per-workflow webhook bookkeeping disappears.

    Provider-specific signature/timestamp headers are not enumerated here —
    each provider's ``verify_inbound_signature`` reads its own headers from
    the dict, so adding a new provider doesn't require changes to this route.
    """
    from api.services.telephony import registry as telephony_registry

    logger.info("Inbound /run dispatch received")

    try:
        webhook_data, raw_body = await parse_webhook_request(request)
        headers = dict(request.headers)

        provider_class = await _detect_provider(webhook_data, headers)
        if not provider_class:
            logger.error("Unable to detect provider for /inbound/run webhook")
            return generic_hangup_response()

        normalized_data = normalize_webhook_data(provider_class, webhook_data, headers)
        logger.info(
            f"/inbound/run normalized data — provider={normalized_data.provider} "
            f"to={normalized_data.to_number} from={normalized_data.from_number}"
        )

        if normalized_data.direction != "inbound":
            logger.warning(
                f"Non-inbound call on /inbound/run: {normalized_data.direction}"
            )
            return generic_hangup_response()

        # 1. Resolve (config, phone_number) in a single SQL roundtrip that
        # joins telephony_configurations and telephony_phone_numbers and
        # filters on (provider, credentials[account_id_field], called number
        # canonical address, is_active). The phone-number row's existence in
        # the matched config simultaneously identifies the org — we never
        # match a config from one org against a phone owned by another.
        spec = telephony_registry.get_optional(provider_class.PROVIDER_NAME)
        account_field = spec.account_id_credential_field if spec else ""

        match = await db_client.find_inbound_route_by_account(
            provider=provider_class.PROVIDER_NAME,
            account_id_field=account_field,
            account_id=normalized_data.account_id or "",
            to_number=normalized_data.to_number,
            country_hint=normalized_data.to_country,
        )

        if not match:
            # The account id is not always there to match on. Plivo omits
            # AuthID from some webhooks — `PlivoProvider.validate_account_id`
            # says so and tolerates it — and a number held on a subaccount
            # reports the subaccount's id, which never equals the parent
            # credential we stored. Both cases refused a correctly configured
            # number with "not configured", which is unfalsifiable from the
            # caller's end: the phone just does not work.
            #
            # The fallback matches on the called number alone and refuses when
            # more than one active row matches, so it can resolve the missing
            # account id but never resolve an ambiguity by guessing. The
            # signature check below still runs against the matched config's own
            # credentials, so this widens which row we find, not who we trust.
            match = await db_client.find_inbound_route_by_number(
                provider=provider_class.PROVIDER_NAME,
                to_number=normalized_data.to_number,
                country_hint=normalized_data.to_country,
            )
            if match:
                logger.info(
                    f"/inbound/run: matched {normalized_data.to_number} by "
                    f"number after the account lookup missed "
                    f"(account_id={normalized_data.account_id!r})"
                )

        if not match:
            logger.warning(
                f"/inbound/run: no inbound route matched "
                f"provider={provider_class.PROVIDER_NAME} "
                f"account_id={normalized_data.account_id} "
                f"to={normalized_data.to_number}"
            )
            return provider_class.generate_validation_error_response(
                TelephonyError.PHONE_NUMBER_NOT_CONFIGURED
            )

        config, phone_row = match
        telephony_configuration_id = config.id

        # A suspended or released number must not reach an agent, even though
        # the carrier still routes to us — we stopped being paid for it. The
        # caller hears the provider's rejection rather than a working agent on
        # a number that is no longer being paid for.
        try:
            number_lifecycle.assert_number_may_serve(phone_row)
        except number_lifecycle.NumberSuspended as exc:
            logger.warning(
                f"/inbound/run: number {normalized_data.to_number} is "
                f"{exc.status}; rejecting the call"
            )
            return provider_class.generate_validation_error_response(
                TelephonyError.PHONE_NUMBER_NOT_CONFIGURED
            )

        # Callback mode: this number is never answered. The caller rings, hangs
        # up, and an agent rings them back — which is the whole point, because
        # in India an incoming call is free and an outgoing one is not, so
        # answering would charge the prospect for the privilege of hearing us.
        # Rejecting before any media is set up means neither side pays for the
        # inbound leg.
        #
        # `inbound_workflow_id` wins when both are set: a number that can be
        # answered is answered.
        if not phone_row.inbound_workflow_id and missed_call.is_callback_number(
            phone_row
        ):
            await _record_missed_call(
                config.organization_id, phone_row, normalized_data
            )
            return generic_hangup_response()

        if not phone_row.inbound_workflow_id:
            logger.warning(
                f"/inbound/run: number {normalized_data.to_number} has no "
                f"inbound_workflow_id assigned"
            )
            return provider_class.generate_validation_error_response(
                TelephonyError.WORKFLOW_NOT_FOUND
            )

        workflow_id = phone_row.inbound_workflow_id
        workflow = await db_client.get_workflow(
            workflow_id, organization_id=config.organization_id
        )
        if not workflow:
            logger.warning(
                f"/inbound/run: workflow not found {workflow_id} for org {config.organization_id}"
            )
            return provider_class.generate_validation_error_response(
                TelephonyError.WORKFLOW_NOT_FOUND
            )

        # An agent the operator has switched off, or archived, does not answer.
        # Rejected here rather than further in: the caller hears the provider's
        # rejection instead of a half-set-up call, and no concurrency slot or
        # workflow run is spent on a call nobody wanted taken.
        try:
            liveness.assert_workflow_may_take_calls(workflow)
        except liveness.AgentNotTakingCalls as exc:
            logger.info(f"/inbound/run: {exc}")
            return provider_class.generate_validation_error_response(
                TelephonyError.WORKFLOW_NOT_FOUND
            )

        # NOTE: the provider-key readiness gate deliberately does NOT run here.
        #
        # It did, briefly, and it broke inbound calling in production. Two
        # reasons, either of which is enough:
        #
        # * It asks about every BYOK section, including `realtime` and
        #   `embeddings`. A voice pipeline uses neither, so an account with a
        #   leftover embeddings vendor and no key for it had every inbound call
        #   refused over a slot the call would never touch.
        # * It resolves `workflow.workflow_configurations`, while the call
        #   actually runs on `workflow_run.definition.workflow_configurations`.
        #   Refusing a call based on a configuration it will not use is a guess
        #   dressed as a check.
        #
        # An inbound caller is a member of the public who cannot fix any of
        # this, so the cost of a false refusal is entirely theirs and entirely
        # invisible to the operator. The outbound test-call path keeps the gate:
        # there a human is watching, the message is actionable, and a false
        # refusal is an inconvenience rather than a phone that stops working.

        user_id = workflow.user_id

        # 3. Verify webhook signature against the matched config's credentials.
        provider_instance = await get_telephony_provider_by_id(
            telephony_configuration_id, config.organization_id
        )
        signature_valid = await provider_instance.verify_inbound_signature(
            str(request.url), webhook_data, headers, raw_body
        )
        if not signature_valid:
            logger.warning(
                f"/inbound/run: signature validation failed for "
                f"{provider_class.PROVIDER_NAME}"
            )
            return provider_class.generate_validation_error_response(
                TelephonyError.SIGNATURE_VALIDATION_FAILED
            )

        # 4. Who is calling, and may they. Before the concurrency slot: a
        # refused caller should not spend one, and before the run is created,
        # because a run recorded for a call we never answered is a row that
        # looks like a failure in every report that counts them.
        decision = await inbound_guard.evaluate(
            caller=normalized_data.from_number,
            phone_number=phone_row,
            lookup_contact=db_client.find_contact_by_phone,
        )
        if not decision.allowed:
            logger.info(
                f"/inbound/run: refusing call from {normalized_data.from_number} "
                f"to {normalized_data.to_number}: {decision.reason}"
            )
            return provider_class.generate_validation_error_response(
                TelephonyError.WORKFLOW_NOT_FOUND
            )

        try:
            concurrency_slot = await call_concurrency.acquire_org_slot(
                config.organization_id,
                source=f"inbound:{provider_class.PROVIDER_NAME}",
                timeout=0,
            )
        except CallConcurrencyLimitError:
            return provider_class.generate_validation_error_response(
                TelephonyError.CONCURRENT_CALL_LIMIT
            )

        workflow_run_id = None
        try:
            # 5. Create workflow run + authorize quota before returning provider
            # stream instructions.
            workflow_run_id = await _create_inbound_workflow_run(
                workflow_id,
                user_id,
                config.organization_id,
                provider_class.PROVIDER_NAME,
                normalized_data,
                telephony_configuration_id=telephony_configuration_id,
                from_phone_number_id=phone_row.id,
                contact_context=decision.contact_context,
            )
            await call_concurrency.bind_workflow_run(concurrency_slot, workflow_run_id)

            quota_result = await authorize_workflow_run_start(
                workflow_id=workflow_id,
                organization_id=config.organization_id,
                workflow_run_id=workflow_run_id,
            )
            if not quota_result.has_quota:
                logger.warning(
                    f"User {user_id} has exceeded quota: {quota_result.error_message}"
                )
                await call_concurrency.release_workflow_run_slot(workflow_run_id)
                return provider_class.generate_validation_error_response(
                    TelephonyError.QUOTA_EXCEEDED
                )

            backend_endpoint, _ = await get_backend_endpoints()
            websocket_url = await stream_capability.stream_url(
                workflow_id=workflow_id,
                organization_id=config.organization_id,
                workflow_run_id=workflow_run_id,
            )

            return await provider_instance.start_inbound_stream(
                websocket_url=websocket_url,
                workflow_run_id=workflow_run_id,
                normalized_data=normalized_data,
                backend_endpoint=backend_endpoint,
            )
        except WorkflowRunSlotAlreadyBoundError:
            return provider_class.generate_validation_error_response(
                TelephonyError.CONCURRENT_CALL_LIMIT
            )
        except Exception:
            if workflow_run_id:
                await call_concurrency.release_workflow_run_slot(workflow_run_id)
            else:
                await call_concurrency.release_slot(concurrency_slot)
            raise

    except ValueError as e:
        logger.error(f"/inbound/run request parsing error: {e}")
        return generic_hangup_response()
    except Exception as e:
        logger.error(f"/inbound/run unexpected error: {e}")
        return generic_hangup_response()


@router.post("/inbound/fallback")
async def handle_inbound_fallback(request: Request):
    """Fallback endpoint that returns audio message when calls cannot be processed."""

    webhook_data, _ = await parse_webhook_request(request)
    headers = dict(request.headers)

    # Detect provider
    provider_class = await _detect_provider(webhook_data, headers)

    if provider_class:
        # Use provider-specific error response
        call_id = (
            webhook_data.get("CallSid")
            or webhook_data.get("CallUUID")
            or webhook_data.get("call_uuid")
        )
        logger.info(
            f"[fallback] Received {provider_class.PROVIDER_NAME} callback for call {call_id}: {json.dumps(webhook_data)}"
        )

        return provider_class.generate_error_response(
            "SYSTEM_UNAVAILABLE",
            "Our system is temporarily unavailable. Please try again later.",
        )
    else:
        # Unknown provider - return generic XML
        logger.info(
            f"[fallback] Received unknown provider callback: {json.dumps(webhook_data)} and request headers: {json.dumps(headers)}"
        )

        return generic_hangup_response()


@router.post("/inbound/{workflow_id}", deprecated=True)
async def handle_inbound_telephony(
    workflow_id: int,
    request: Request,
):
    """[LEGACY] Per-workflow inbound webhook.

    Superseded by ``POST /inbound/run``, which resolves the workflow from
    the called number's ``inbound_workflow_id`` and lets a single webhook
    URL serve every workflow in the org. New integrations should point
    their provider at ``/inbound/run``; this route is kept only for
    existing provider configurations that still encode ``workflow_id``
    in the URL.
    """
    logger.info(
        f"[legacy /inbound/{{workflow_id}}] Inbound call received for workflow_id: {workflow_id}"
    )

    try:
        webhook_data, raw_body = await parse_webhook_request(request)
        logger.info(f"Inbound call data: {dict(webhook_data)}")
        headers = dict(request.headers)

        # Detect provider and normalize data
        provider_class = await _detect_provider(webhook_data, headers)
        if not provider_class:
            logger.error("Unable to detect provider for webhook")
            return generic_hangup_response()

        normalized_data = normalize_webhook_data(provider_class, webhook_data, headers)

        logger.info(f"Inbound call - Provider: {normalized_data.provider}")
        logger.info(f"Normalized data: {normalized_data}")

        # Validate inbound direction
        if normalized_data.direction != "inbound":
            logger.warning(f"Non-inbound call received: {normalized_data.direction}")
            return generic_hangup_response()

        (
            is_valid,
            error_type,
            workflow_context,
            provider_instance,
        ) = await _validate_inbound_request(
            workflow_id,
            str(request.url),
            provider_class,
            normalized_data,
            webhook_data,
            headers,
            raw_body,
        )

        if not is_valid:
            logger.error(f"Request validation failed: {error_type}")
            return provider_class.generate_validation_error_response(error_type)

        user_id = workflow_context["user_id"]
        organization_id = workflow_context["organization_id"]
        try:
            concurrency_slot = await call_concurrency.acquire_org_slot(
                organization_id,
                source=f"inbound_legacy:{workflow_context['provider']}",
                timeout=0,
            )
        except CallConcurrencyLimitError:
            return provider_class.generate_validation_error_response(
                TelephonyError.CONCURRENT_CALL_LIMIT
            )

        workflow_run_id = None
        try:
            # Create workflow run.
            workflow_run_id = await _create_inbound_workflow_run(
                workflow_id,
                workflow_context["user_id"],
                organization_id,
                workflow_context["provider"],
                normalized_data,
                telephony_configuration_id=workflow_context[
                    "telephony_configuration_id"
                ],
                from_phone_number_id=workflow_context.get("from_phone_number_id"),
            )
            await call_concurrency.bind_workflow_run(concurrency_slot, workflow_run_id)

            quota_result = await authorize_workflow_run_start(
                workflow_id=workflow_id,
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
            )
            if not quota_result.has_quota:
                logger.warning(
                    f"User {user_id} has exceeded quota for inbound calls: "
                    f"{quota_result.error_message}"
                )
                await call_concurrency.release_workflow_run_slot(workflow_run_id)
                return provider_class.generate_validation_error_response(
                    TelephonyError.QUOTA_EXCEEDED
                )

            # Generate response URLs
            backend_endpoint, _ = await get_backend_endpoints()
            websocket_url = await stream_capability.stream_url(
                workflow_id=workflow_id,
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
            )

            response = await provider_instance.start_inbound_stream(
                websocket_url=websocket_url,
                workflow_run_id=workflow_run_id,
                normalized_data=normalized_data,
                backend_endpoint=backend_endpoint,
            )
        except WorkflowRunSlotAlreadyBoundError:
            return provider_class.generate_validation_error_response(
                TelephonyError.CONCURRENT_CALL_LIMIT
            )
        except Exception:
            if workflow_run_id:
                await call_concurrency.release_workflow_run_slot(workflow_run_id)
            else:
                await call_concurrency.release_slot(concurrency_slot)
            raise

        logger.info(
            f"Generated {normalized_data.provider} response for call {normalized_data.call_id}"
        )
        return response

    except ValueError as e:
        logger.error(f"Request parsing error: {e}")
        return generic_hangup_response()
    except Exception as e:
        logger.error(f"Error processing inbound call: {e}")
        return generic_hangup_response()


@router.post("/transfer-result/{transfer_id}")
async def complete_transfer_function_call(transfer_id: str, request: Request):
    """Webhook endpoint to complete the function call with transfer result.

    Called by Twilio's StatusCallback when the transfer call status changes.
    """
    form_data = await request.form()
    data = dict(form_data)

    call_status = data.get("CallStatus", "")
    call_sid = data.get("CallSid", "")

    logger.info(
        f"Transfer result(call status) webhook: {transfer_id} status={call_status}"
    )

    # Get transfer context from Redis for additional information
    call_transfer_manager = await get_call_transfer_manager()
    transfer_context = await call_transfer_manager.get_transfer_context(transfer_id)

    original_call_sid = transfer_context.original_call_sid if transfer_context else None
    conference_name = transfer_context.conference_name if transfer_context else None

    # Determine the result based on call status with user-friendly messaging
    if call_status in ("in-progress", "answered") and not destination_is_human(
        data.get("AnsweredBy")
    ):
        # Answered, but by a machine. Bridging here would put a caller who
        # asked for a person into a voicemail greeting and then silence;
        # failing hands the conversation back to the agent, which can say the
        # line was unavailable and offer a callback.
        logger.info(
            f"Transfer {transfer_id} answered by {data.get('AnsweredBy')}; "
            "not bridging the caller"
        )
        result = {
            "status": "transfer_failed",
            "reason": "answered_by_machine",
            "message": (
                "The transfer reached a voicemail rather than a person. "
                "Let me take a message instead."
            ),
            "action": "transfer_failed",
            "call_sid": call_sid,
            "end_call": True,
        }
    elif call_status in ("in-progress", "answered"):
        result = {
            "status": "success",
            "message": "Great! The destination number answered. Let me transfer you now.",
            "action": "destination_answered",
            "conference_id": conference_name,
            "transfer_call_sid": call_sid,  # The outbound transfer call SID
            "original_call_sid": original_call_sid,  # The original caller's SID
            "end_call": False,  # Continue with transfer
        }
    elif call_status == "no-answer":
        result = {
            "status": "transfer_failed",
            "reason": "no_answer",
            "message": "The transfer call was not answered. The person may be busy or unavailable right now.",
            "action": "transfer_failed",
            "call_sid": call_sid,
            "end_call": True,
        }
    elif call_status == "busy":
        result = {
            "status": "transfer_failed",
            "reason": "busy",
            "message": "The transfer call encountered a busy signal. The person is likely on another call.",
            "action": "transfer_failed",
            "call_sid": call_sid,
            "end_call": True,
        }
    elif call_status == "failed":
        result = {
            "status": "transfer_failed",
            "reason": "call_failed",
            "message": "The transfer call failed to connect. There may be a network issue or the number is unavailable.",
            "action": "transfer_failed",
            "call_sid": call_sid,
            "end_call": True,
        }
    else:
        # Intermediate status (ringing, in-progress, etc.), don't complete yet
        logger.info(
            f"Received intermediate status {call_status}, waiting for final status"
        )
        return {"status": "pending"}

    # Complete the function call with Redis event publishing
    try:
        # Determine event type based on result status
        if result["status"] == "success":
            event_type = TransferEventType.DESTINATION_ANSWERED
        else:
            event_type = TransferEventType.TRANSFER_FAILED

        transfer_event = TransferEvent(
            type=event_type,
            transfer_id=transfer_id,
            original_call_sid=original_call_sid or "",
            transfer_call_sid=call_sid,
            conference_name=conference_name,
            message=result.get("message", ""),
            status=result["status"],
            action=result.get("action", ""),
            reason=result.get("reason"),
        )

        # Publish the event via Redis
        await call_transfer_manager.publish_transfer_event(transfer_event)
        logger.info(
            f"Published {event_type} event for {transfer_id} with result: {result['status']}"
        )

    except Exception as e:
        logger.error(f"Error completing transfer {transfer_id}: {e}")

    return {"status": "completed", "result": result}


@router.post("/verification/voice/{token}")
async def verification_voice_answer(token: str):
    """What the carrier fetches when someone answers a verification call.

    Unauthenticated by necessity — Plivo fetches it, not a signed-in user — and
    safe because the token is the only thing it accepts: 24 random bytes, alive
    for five minutes, and spent the first time it is read. Guessing one is
    harder than guessing the six-digit code it protects.

    Returns XML either way. A carrier that gets an error page reads it as a
    failed call and the person who answered hears nothing, which is a worse
    outcome than being told the call expired.
    """
    spoken = await voice_otp.claim(token)
    if spoken is None:
        logger.warning(
            "Verification voice answer for an unknown or spent token {}...",
            token[:6],
        )
        return Response(
            content=voice_otp.unavailable_xml(), media_type="application/xml"
        )

    # The code itself is never logged. It is the secret the call exists to
    # deliver, and a log line is a place it outlives the five minutes.
    logger.info("Reading a verification code out on a call (token {}...).", token[:6])
    return Response(content=voice_otp.speak_xml(spoken), media_type="application/xml")


# Mount per-provider routers (webhook, status callbacks, answer URLs).
#
# Each provider's routes live at ``providers/<name>/routes.py`` and expose
# a module-level ``router``. We discover them through the registry rather
# than pre-importing them from each provider's __init__.py so that the
# (heavy) route module — which transitively depends on status_processor,
# campaign helpers, etc. — is only loaded when the HTTP layer is actually
# being wired up, not when someone merely asks for a TelephonyProvider
# class. This is what keeps the package init free of cycles.
def _mount_provider_routers() -> None:
    import importlib

    from api.services.telephony import registry as _telephony_registry

    for spec in _telephony_registry.all_specs():
        try:
            module = importlib.import_module(
                f"api.services.telephony.providers.{spec.name}.routes"
            )
        except ModuleNotFoundError:
            # Provider has no routes (e.g. ARI, which only has a WebSocket).
            continue
        provider_router = getattr(module, "router", None)
        if provider_router is not None:
            router.include_router(provider_router)


_mount_provider_routers()
