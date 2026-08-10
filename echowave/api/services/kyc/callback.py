"""Handling Plivo's compliance status callbacks.

Plivo posts here whenever an application changes state. That makes this the
path by which an account becomes able to place calls — and the path by which a
suspension takes that away — so two properties matter more than anything else
in this module.

**It must not accept an unsigned request.** An unauthenticated endpoint that
sets ``carrier_approved`` is an endpoint that opens telephony for anyone who
can find the URL. Verification failure is a 403 and nothing else happens.

**It must be safe to run twice.** Plivo redelivers on any non-2xx and can
redeliver on a timeout it decided about after we committed, so the same verdict
arrives more than once in normal operation. Repeat delivery of a verdict
already recorded is a no-op that still returns 200; returning an error would
earn another redelivery of something we handled correctly the first time.

Callbacks replace the status poll this codebase used to run. A poll asked a
question we are now told the answer to, against the tighter of Plivo's two rate
limits, on a schedule that added latency to every approval.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass

from loguru import logger

from api.constants import PLATFORM_PLIVO_AUTH_TOKEN
from api.db import db_client
from api.services.kyc import service as kyc_service
from api.services.kyc.carrier import (
    UnknownCarrierStatus,
    plivo_status_to_verdict,
)
from api.services.kyc.state import KycTransitionError
from api.services.telephony.providers.plivo.provider import PlivoProvider


@dataclass(frozen=True)
class CallbackOutcome:
    """What handling one callback did, for the response and the logs."""

    handled: bool
    status: str | None = None
    organization_id: int | None = None
    detail: str = ""


def verify_signature(
    *,
    url: str,
    params: dict,
    signature: str | None,
    nonce: str | None,
    auth_token: str | None = None,
) -> bool:
    """Plivo's V3 webhook signature, over our own account's auth token.

    Reuses ``PlivoProvider``'s URL construction rather than reimplementing it.
    The canonicalisation is fiddly — sorted parameters, an empty-post-params
    special case — and a second copy of it would drift from the one the call
    webhooks use, leaving a signature scheme that works on one endpoint and
    silently rejects on another.
    """
    token = auth_token or PLATFORM_PLIVO_AUTH_TOKEN
    if not token:
        logger.error(
            "Cannot verify a Plivo compliance callback: "
            "PLATFORM_PLIVO_AUTH_TOKEN is not set. Refusing the request."
        )
        return False
    if not signature or not nonce:
        return False

    payload = f"{PlivoProvider._construct_post_url(url, params or {})}.{nonce}"
    computed = base64.b64encode(
        hmac.new(
            token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).digest()
    ).decode("utf-8")

    return any(
        hmac.compare_digest(computed, candidate.strip())
        for candidate in signature.split(",")
        if candidate.strip()
    )


def extract_compliance_id(payload: dict) -> str | None:
    for key in (
        "compliance_application_id",
        "compliance_id",
        "application_id",
        "id",
    ):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def extract_status(payload: dict) -> str | None:
    for key in ("status", "compliance_status", "application_status"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def extract_reason(payload: dict) -> str | None:
    for key in ("rejection_reason", "reason", "message", "comment"):
        value = payload.get(key)
        if value:
            return str(value)[:2000]
    return None


async def handle(payload: dict) -> CallbackOutcome:
    """Apply one compliance callback to the account it belongs to.

    The organization is resolved **from the compliance id we stored when we
    filed the application**, never from anything in the payload. A callback
    body is attacker-influenced data if the signature check is ever weakened;
    taking an organization id from it would make this endpoint a way to
    approve an arbitrary account.
    """
    compliance_id = extract_compliance_id(payload)
    if not compliance_id:
        return CallbackOutcome(
            handled=False, detail="Callback carried no compliance application id."
        )

    raw_status = extract_status(payload)
    try:
        verdict = plivo_status_to_verdict(raw_status)
    except UnknownCarrierStatus as exc:
        # Logged loudly and accepted, so Plivo stops redelivering something we
        # will never understand. The account stays where it is, which is the
        # conservative outcome for a status we cannot interpret.
        logger.error("Unusable Plivo compliance callback: {}", exc)
        return CallbackOutcome(
            handled=False, status=raw_status, detail="Unrecognised status."
        )

    record = await db_client.get_kyc_by_carrier_reference(compliance_id)
    if record is None:
        logger.warning(
            "Plivo compliance callback for application {} matches no account",
            compliance_id,
        )
        return CallbackOutcome(
            handled=False,
            status=raw_status,
            detail="No account holds this compliance application.",
        )

    try:
        view = await kyc_service.record_carrier_verdict(
            organization_id=record.organization_id,
            verdict=verdict,
            raw_status=raw_status,
            rejection_reason=extract_reason(payload),
        )
    except KycTransitionError as exc:
        # A verdict that does not fit where the record currently sits. Recorded
        # rather than raised: Plivo's ordering is not guaranteed, so a stale
        # callback arriving after a newer one is expected, not exceptional.
        logger.warning(
            "Plivo compliance callback {} for org {} does not apply: {}",
            raw_status,
            record.organization_id,
            exc,
        )
        return CallbackOutcome(
            handled=False,
            status=raw_status,
            organization_id=record.organization_id,
            detail="Verdict does not apply to the account's current state.",
        )

    if view is None:
        return CallbackOutcome(
            handled=True,
            status=raw_status,
            organization_id=record.organization_id,
            detail="No change.",
        )

    logger.info(
        "Plivo compliance callback moved org {} to {} (application {})",
        record.organization_id,
        view.status,
        compliance_id,
    )
    return CallbackOutcome(
        handled=True,
        status=raw_status,
        organization_id=record.organization_id,
        detail=view.status,
    )
