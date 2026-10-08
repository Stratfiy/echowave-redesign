"""Phone and verification, as one lifecycle (handoff 7, 25; screen 24).

States: not_requested, pending_verification, rejected (with the next step),
eligible, provisioning, active, suspended, released. Derived from what
already exists -- telephony verification (services/kyc), the workspace's
numbers, the autopay mandate -- never stored twice.

* **Chat never waits on this.** Nothing here gates anything but a number.
* **Each source is read on its own.** A failed read is that source's
  "could not check", never "none" (handoff 30): a carrier list that failed
  is not "no numbers", and an assignment is offered only when the helper
  list was read.
* **A number is "active" only after a real test**: an incoming call and a
  handover to a person, recorded against the number (``number_readiness``).
  Until then it reads "set up, not tested yet".
* **Who pays is not decided** ("Who pays for numbers in the beta" is open).
  Until ``NUMBER_PAYMENT_POLICY`` is set, requesting a number is unavailable
  and says why. The amount is a marked placeholder; no price is invented.
* A request pays, so it is an action card (services/identity/cards.py).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from api import constants
from api.db import db_client
from api.db.identity_models import NumberReadinessModel
from api.db.models import TelephonyConfigurationModel, TelephonyPhoneNumberModel
from api.enums import KycStatus
from api.services import features
from api.services.workflow import audit_log

FLAG = "identity_phone"

#: The founder's to decide (LAUNCH-PLAN.md "Who pays for numbers in the
#: beta"). Shown verbatim so nobody reads it as a price.
AMOUNT_PLACEHOLDER = "[PLACEHOLDER: monthly amount, founder decision pending]"

_POLICIES = {
    "sponsored": "During the beta Decibyl pays the provider for the number. [PLACEHOLDER: sponsorship terms, founder decision pending]",
    "provider_autopay": (
        "You authorise autopay with the payment provider once; the provider "
        "charges the number's rent every month until you release it."
    ),
}
_ADDRESS = re.compile(r"^\+?[1-9][0-9]{7,14}$")


def enabled(organization_id: int | None) -> bool:
    return features.is_on(FLAG, organization_id)


def payment_policy() -> dict[str, Any]:
    key = constants.NUMBER_PAYMENT_POLICY or None
    decided = key in _POLICIES
    return {
        "key": key if decided else None,
        "decided": decided,
        "who_pays": _POLICIES.get(
            key or "", "Who pays for numbers in the beta is not decided yet."
        ),
        "amount": AMOUNT_PLACEHOLDER,
        # How paying for a number works, step by step, whatever the policy
        # (screen 24: "a concise cost explanation" before any request).
        "steps": [
            "A number is rented from a telephone carrier, month by month.",
            "The carrier requires the business to be verified first.",
            "Before a number is requested, the cost and who pays it are shown on the card you confirm.",
            "Rent continues every month until the number is released.",
            "Releasing a number stops the rent; the number may go to someone else afterwards.",
        ],
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


async def _read(name: str, sources: dict[str, str], reader) -> Any:
    try:
        value = await reader()
        sources[name] = "ok"
        return value
    except Exception as exc:  # noqa: BLE001 - a failed source is a state, not a 500
        logger.warning("Phone view: could not read {}: {}", name, exc)
        sources[name] = "failed"
        return None


async def _numbers(organization_id: int) -> list[Any]:
    async with db_client.async_session() as session:
        rows = await session.execute(
            select(TelephonyPhoneNumberModel, NumberReadinessModel)
            .outerjoin(
                NumberReadinessModel,
                NumberReadinessModel.phone_number_id == TelephonyPhoneNumberModel.id,
            )
            .where(
                TelephonyPhoneNumberModel.organization_id == organization_id,
                TelephonyPhoneNumberModel.is_shared_outbound.is_(False),
            )
            .order_by(TelephonyPhoneNumberModel.id)
        )
        return list(rows.all())


async def _mandate(organization_id: int) -> dict[str, Any]:
    from api.services.billing import mandates

    async with db_client.async_session() as session:
        mandate = await mandates.get_mandate(session, organization_id=organization_id)
    return {
        "required": bool(constants.REQUIRE_MANDATE_FOR_NUMBERS),
        "authorised": mandates.is_authorised(mandate),
        "status": mandate.status if mandate else None,
    }


async def _helpers(organization_id: int) -> list[dict[str, Any]]:
    rows = await db_client.get_all_workflows_for_listing(
        organization_id=organization_id
    )
    return [{"id": w.id, "name": w.name} for w in rows]


_PENDING = {
    KycStatus.SUBMITTED.value,
    KycStatus.UNDER_REVIEW.value,
    KycStatus.FORWARDED.value,
}
_REJECTED = {KycStatus.REJECTED.value, KycStatus.CARRIER_REJECTED.value}
_SUSPENDED = {KycStatus.SUSPENDED.value, KycStatus.EXPIRED.value}


def _number_state(number: Any, readiness: Any) -> str:
    if number.status == "released":
        return "released"
    if number.status == "suspended":
        return "suspended"
    if readiness and readiness.incoming_call_ok_at and readiness.escalation_ok_at:
        return "active"
    return "provisioning"


async def view(organization_id: int) -> dict[str, Any]:
    sources: dict[str, str] = {}
    kyc = await _read(
        "verification", sources, lambda: db_client.get_kyc(organization_id)
    )
    numbers = await _read("numbers", sources, lambda: _numbers(organization_id))
    mandate = await _read("autopay", sources, lambda: _mandate(organization_id))
    helpers = await _read("helpers", sources, lambda: _helpers(organization_id))
    status = (
        (kyc.status if kyc else KycStatus.NOT_STARTED.value)
        if sources["verification"] == "ok"
        else None
    )

    rows = []
    for number, readiness in numbers or []:
        rows.append(
            {
                "id": number.id,
                "address": number.address,
                "label": number.label,
                "state": _number_state(number, readiness),
                "assigned_helper_id": number.inbound_workflow_id,
                "incoming_call_ok_at": _iso(readiness.incoming_call_ok_at)
                if readiness
                else None,
                "escalation_ok_at": _iso(readiness.escalation_ok_at)
                if readiness
                else None,
                "provisioned_at": _iso(number.provisioned_at),
            }
        )
    live = [r for r in rows if r["state"] not in ("released",)]

    next_step: str | None
    if status is None:
        state, next_step = (
            "unknown",
            "We could not check verification just now. Try again.",
        )
    elif any(r["state"] == "active" for r in live):
        state, next_step = "active", None
    elif any(r["state"] == "suspended" for r in live) or status in _SUSPENDED:
        state, next_step = "suspended", "Ask Help what is needed to restore it."
    elif any(r["state"] == "provisioning" for r in live):
        state, next_step = (
            "provisioning",
            "Make a test call to the number and hand it over to a person; it is labelled ready after both pass.",
        )
    elif rows and not live:
        state, next_step = "released", "Request a new number when you need one."
    elif status == KycStatus.CARRIER_APPROVED.value:
        state, next_step = "eligible", "Request a number for one of your helpers."
    elif status in _PENDING:
        state, next_step = (
            "pending_verification",
            "Verification is with the reviewers. Nothing to do now.",
        )
    elif status in _REJECTED:
        reason = (kyc.carrier_rejection_reason or kyc.rejection_reason or "").strip()
        state = "rejected"
        next_step = (
            f"Verification was not accepted: {reason[:200]}. Fix it and submit again."
            if reason
            else "Verification was not accepted. Check the details and submit again."
        )
    else:
        state, next_step = (
            "not_requested",
            "Verify the business to get a number. Chat works without one.",
        )

    policy = payment_policy()
    can_request = (
        status == KycStatus.CARRIER_APPROVED.value
        and policy["decided"]
        and sources.get("helpers") == "ok"
        and bool(helpers)
    )
    if not policy["decided"]:
        request_reason = "Requesting a number opens once who pays for numbers in the beta is decided."
    elif status != KycStatus.CARRIER_APPROVED.value:
        request_reason = "The business has to be verified first."
    elif sources.get("helpers") != "ok":
        request_reason = (
            "We could not load your helpers, so a number cannot be assigned right now."
        )
    elif not helpers:
        request_reason = "Make a helper first; a number is assigned to one."
    else:
        request_reason = None
    return {
        "state": state,
        "next_step": next_step,
        "verification_status": status,
        "sources": sources,
        "numbers": rows if sources.get("numbers") == "ok" else None,
        "helpers": helpers if sources.get("helpers") == "ok" else None,
        "autopay": mandate if sources.get("autopay") == "ok" else None,
        "payment": policy,
        "request": {"available": can_request, "reason": request_reason},
        # Chat, learning and everything else never wait on this.
        "chat_needs_number": False,
    }


async def _managed_configuration(organization_id: int) -> int:
    """The workspace's platform-managed carrier account, which verification
    creates; the one a requested number is bought on."""
    from api.services.identity.cards import CardError

    async with db_client.async_session() as session:
        config_id = await session.scalar(
            select(TelephonyConfigurationModel.id)
            .where(
                TelephonyConfigurationModel.organization_id == organization_id,
                TelephonyConfigurationModel.is_platform_managed.is_(True),
            )
            .order_by(TelephonyConfigurationModel.id)
            .limit(1)
        )
    if config_id is None:
        raise CardError("There is no managed carrier account here yet.")
    return int(config_id)


async def check_request(
    organization_id: int,
    *,
    telephony_configuration_id: Any,
    address: Any,
    helper_id: Any,
) -> dict[str, Any]:
    """Everything a number request must satisfy, checked before the card is
    proposed and again when it runs. Raises CardError."""
    from api.services.identity.cards import CardError

    policy = payment_policy()
    if not policy["decided"]:
        raise CardError(
            "Requesting a number opens once who pays for numbers in the beta is decided."
        )
    kyc = await db_client.get_kyc(organization_id)
    if not kyc or kyc.status != KycStatus.CARRIER_APPROVED.value:
        raise CardError("The business has to be verified before a number is requested.")
    try:
        config_id = (
            int(telephony_configuration_id)
            if telephony_configuration_id is not None
            else await _managed_configuration(organization_id)
        )
        helper = int(helper_id)
    except (TypeError, ValueError) as exc:
        raise CardError("Say which number and which helper.") from exc
    config = await db_client.get_telephony_configuration(config_id)
    if config is None or config.organization_id != organization_id:
        raise CardError("That carrier account is not here.")
    workflow = await db_client.get_workflow(helper, organization_id=organization_id)
    if workflow is None:
        raise CardError("That helper is not here.")
    number = re.sub(r"[\s\-()]", "", str(address or ""))
    if not _ADDRESS.match(number):
        raise CardError("That is not a phone number.")
    if policy["key"] == "provider_autopay":
        mandate = await _mandate(organization_id)
        if mandate["required"] and not mandate["authorised"]:
            raise CardError("Authorise autopay with the payment provider first.")
    return {
        "telephony_configuration_id": config_id,
        "address": number,
        "helper_id": helper,
        "helper_name": workflow.name,
    }


async def provision(organization_id: int, owner: int, args: dict[str, Any]) -> str:
    """Run by the confirmed card: buy and assign. A refusal raises CardError
    (nothing bought); anything else propagates as outcome unknown."""
    from api.services.billing.mandates import MandateNotAuthorised
    from api.services.compliance.agreements import AgreementsOutstanding
    from api.services.identity.cards import CardError
    from api.services.telephony import provisioning

    checked = await check_request(
        organization_id,
        telephony_configuration_id=args.get("telephony_configuration_id"),
        address=args.get("address"),
        helper_id=args.get("helper_id"),
    )
    try:
        result = await provisioning.provision(
            organization_id=organization_id,
            telephony_configuration_id=checked["telephony_configuration_id"],
            address=checked["address"],
            inbound_workflow_id=checked["helper_id"],
        )
    except (
        provisioning.NotVerified,
        provisioning.ProvisioningError,
        MandateNotAuthorised,
        AgreementsOutstanding,
    ) as exc:
        raise CardError(f"Not bought: {exc}") from exc
    await audit_log.record(
        organization_id,
        action="number_requested",
        subject_kind="phone_number",
        subject_id=result.phone_number_id,
        subject=str(result.address)[:255],
        actor_user_id=owner,
        before=None,
        after={"helper_id": checked["helper_id"]},
    )
    return (
        f"{result.address} is yours, answering as {checked['helper_name']}. It is "
        "labelled ready after a test call and a handover pass."
    )


async def record_readiness(
    organization_id: int,
    user_id: int,
    phone_number_id: int,
    *,
    incoming_call_ok: bool,
    escalation_ok: bool,
) -> dict[str, Any]:
    """An admin records the test call and the handover. Audited."""
    async with db_client.async_session() as session:
        number = await session.scalar(
            select(TelephonyPhoneNumberModel).where(
                TelephonyPhoneNumberModel.id == phone_number_id,
                TelephonyPhoneNumberModel.organization_id == organization_id,
            )
        )
        if number is None:
            raise LookupError("not found")
        address = number.address
        now = datetime.now(UTC)
        values = {
            "organization_id": organization_id,
            "incoming_call_ok_at": now if incoming_call_ok else None,
            "escalation_ok_at": now if escalation_ok else None,
            "recorded_by": user_id,
            "updated_at": now,
        }
        await session.execute(
            insert(NumberReadinessModel)
            .values(phone_number_id=phone_number_id, **values)
            .on_conflict_do_update(index_elements=["phone_number_id"], set_=values)
        )
        await session.commit()
    await audit_log.record(
        organization_id,
        action="number_readiness_recorded",
        subject_kind="phone_number",
        subject_id=phone_number_id,
        subject=str(address)[:255],
        actor_user_id=user_id,
        before=None,
        after={"incoming_call_ok": incoming_call_ok, "escalation_ok": escalation_ok},
    )
    return await view(organization_id)
