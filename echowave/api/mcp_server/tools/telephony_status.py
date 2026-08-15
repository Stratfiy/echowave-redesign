"""MCP tools for telephony: verification, numbers, and what they cost.

Read-only, all of them. The questions these answer are the ones that otherwise
become support tickets — "why can't my agent call out", "which number does this
agent answer on", "what am I actually paying for" — and every one of them is
currently answerable only by a human opening the dashboard.

**Nothing here spends money or releases a number.** Provisioning and release
live behind the REST API deliberately: an agent that can buy a number can buy
several by retrying, and an agent that can release one can permanently destroy
a phone number that is printed on a customer's signage and cannot be recovered.
Read access is the part that is safe to automate.
"""

from api.db import db_client
from api.enums import PhoneNumberStatus, RecurringChargeStatus
from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tracing import traced_tool
from api.services.kyc import service as kyc_service
from api.services.telephony import provisioning


@traced_tool
async def get_telephony_verification() -> dict:
    """Whether this account may place phone calls, and what is outstanding.

    Indian phone numbers require the telecom licensee to verify each end
    customer, so an account can be fully set up and still unable to dial. This
    is the tool that explains which of those two situations you are in.

    Returns the verification `status`, whether `telephony_enabled` (the only
    thing that actually permits calls — it reflects the carrier's decision, not
    our own review), which documents are `required_documents` and which are
    still `missing_documents`, and any `rejection_reason` from us or
    `carrier_rejection_reason` from the licensee.

    A status of `suspended` means the licensee paused a previously approved
    account — the numbers are still held and it is usually reversible.
    `expired` means the application lapsed and a fresh one is needed.

    `autopay` is the **second** gate and is reported here because being
    approved and still unable to buy a number is otherwise baffling. A number
    is monthly rent from the day it is issued, so the account has to have
    authorised a standing instruction before one is handed over. It reports
    whether autopay is `required`, whether it is `authorised`, and the
    `status` the payment provider last told us — but not the authorisation
    link, which belongs to a browser session rather than to an agent.
    """
    user = await authenticate_mcp_request()
    view = await kyc_service.get_view(user.selected_organization_id)

    from api.constants import REQUIRE_MANDATE_FOR_NUMBERS
    from api.services.billing import mandates as mandate_service

    async with db_client.async_session() as session:
        mandate = await mandate_service.get_mandate(
            session, organization_id=user.selected_organization_id
        )

    return {
        "autopay": {
            "required": REQUIRE_MANDATE_FOR_NUMBERS,
            "authorised": mandate_service.is_authorised(mandate),
            "status": mandate.status if mandate else "not_started",
            "last_failure_reason": (mandate.last_failure_reason if mandate else None),
        },
        "status": view.status,
        "telephony_enabled": view.telephony_enabled,
        "business_type": view.business_type,
        "legal_name": view.legal_name,
        "required_documents": list(view.required_documents),
        "missing_documents": list(view.missing_documents),
        "can_submit": view.can_submit,
        "rejection_reason": view.rejection_reason,
        "carrier_rejection_reason": view.carrier_rejection_reason,
        "submitted_at": view.submitted_at.isoformat() if view.submitted_at else None,
    }


@traced_tool
async def list_phone_numbers() -> list[dict]:
    """List this organization's phone numbers and what answers on each.

    Returns each number's `address`, `status`, whether it `is_active`, the
    `inbound_workflow_id` of the agent that answers it (null means inbound
    calls to this number are rejected), and `carrier_number_id` — set only for
    numbers Decibyl bought on your behalf.

    `status` is distinct from `is_active`. `is_active` is your own on/off
    switch; `status` is ours:

    * `active` — routing normally
    * `suspended` — the monthly rental is unpaid past the grace threshold.
      Calls are blocked, the number is still held, and topping up restores it
      immediately
    * `released` — given back to the carrier. Gone, and not recoverable
    """
    user = await authenticate_mcp_request()
    organization_id = user.selected_organization_id

    configurations = await db_client.list_telephony_configurations(organization_id)
    numbers: list[dict] = []
    for configuration in configurations:
        rows = await db_client.list_phone_numbers_with_workflow_name_for_config(
            configuration.id
        )
        for row, workflow_name in rows:
            numbers.append(
                {
                    "phone_number_id": row.id,
                    "address": row.address,
                    "status": row.status or PhoneNumberStatus.ACTIVE.value,
                    "is_active": row.is_active,
                    "label": row.label,
                    "country_code": row.country_code,
                    "telephony_configuration_id": configuration.id,
                    "telephony_configuration_name": configuration.name,
                    "provider": configuration.provider,
                    "inbound_workflow_id": row.inbound_workflow_id,
                    "inbound_workflow_name": workflow_name,
                    "is_default_caller_id": row.is_default_caller_id,
                    # Present only for numbers we bought and therefore pay
                    # monthly rent on. A customer's own number has none.
                    "carrier_number_id": row.carrier_number_id,
                    "provisioned_at": row.provisioned_at.isoformat()
                    if row.provisioned_at
                    else None,
                }
            )
    return numbers


@traced_tool
async def search_available_numbers(
    telephony_configuration_id: int,
    country_iso: str = "IN",
    number_type: str = "local",
    city: str | None = None,
    limit: int = 10,
) -> dict:
    """Search the carrier for numbers available to buy. Buys nothing.

    Requires the account's telephony verification to be approved — a list of
    numbers you are not permitted to buy is worse than being told what to do
    first, so the check happens before the search rather than at purchase.

    An empty `numbers` list is a normal answer: India local inventory is thin
    and moves. It is not a reason to retry in a loop.

    Purchasing is deliberately not available over MCP. To buy one of these,
    POST to `/api/v1/managed-numbers` — it spends money every month and is a
    decision a person should make.
    """
    user = await authenticate_mcp_request()
    organization_id = user.selected_organization_id

    configuration = await db_client.get_telephony_configuration_for_org(
        telephony_configuration_id, organization_id
    )
    if configuration is None:
        return {
            "error": "No telephony configuration with that id in this organization.",
            "numbers": [],
        }

    try:
        results = await provisioning.search(
            organization_id=organization_id,
            telephony_configuration_id=telephony_configuration_id,
            country_iso=country_iso,
            number_type=number_type,
            city=city,
            limit=max(1, min(limit, 20)),
        )
    except provisioning.NotVerified as exc:
        # Returned rather than raised: "you are not verified yet" is an answer
        # to the question, and the sentence tells the caller what to do.
        return {"error": str(exc), "verification_required": True, "numbers": []}
    except Exception as exc:
        return {"error": f"The carrier could not be reached: {exc}", "numbers": []}

    return {
        "numbers": [
            {
                "number": n.number,
                "number_type": n.number_type,
                "city": n.city,
                "region": n.region,
                "monthly_rental_at_carrier": n.monthly_rental,
            }
            for n in results
        ]
    }


@traced_tool
async def get_billing_summary() -> dict:
    """Credit balance, and every recurring charge this account carries.

    Two things that are easy to confuse and are reported separately here:

    * `balance_paise` — prepaid credit. Calls draw down against it
    * `recurring_charges` — monthly rentals for phone numbers, which accrue
      whether or not anyone dials. This is the cost a quiet month does *not*
      make go away

    Each charge reports `price_paise` (what you pay per month) and its
    `status`. A `past_due` charge is being retried daily and calls still work;
    `suspended` means calls on that number are paused and a top-up restores
    them; `pending_release` means it has been unpaid long enough that the
    number may be given up.

    Amounts are in paise — 100 paise is ₹1 — because money is held as integers
    throughout, never as floats.
    """
    from sqlalchemy import select

    from api.db.models import RecurringChargeModel
    from api.services.billing.costing import current_balance_paise

    user = await authenticate_mcp_request()
    organization_id = user.selected_organization_id

    async with db_client.async_session() as session:
        balance = await current_balance_paise(session, organization_id=organization_id)
        charges = list(
            (
                await session.scalars(
                    select(RecurringChargeModel)
                    .where(
                        RecurringChargeModel.organization_id == organization_id,
                        RecurringChargeModel.status
                        != RecurringChargeStatus.RELEASED.value,
                    )
                    .order_by(RecurringChargeModel.id)
                )
            ).all()
        )

    monthly_total = sum(
        c.price_paise
        for c in charges
        if c.status
        not in (
            RecurringChargeStatus.RELEASED.value,
            RecurringChargeStatus.CANCELLED.value,
        )
    )

    return {
        "balance_paise": balance,
        "balance_inr": round(balance / 100, 2),
        "monthly_recurring_paise": monthly_total,
        "monthly_recurring_inr": round(monthly_total / 100, 2),
        "recurring_charges": [
            {
                "charge_id": c.id,
                "charge_type": c.charge_type,
                "resource_id": c.resource_id,
                "status": c.status,
                "price_paise": c.price_paise,
                "next_charge_at": c.next_charge_at.isoformat()
                if c.next_charge_at
                else None,
                "days_overdue": _days_overdue(c),
                "last_failure_reason": c.last_failure_reason,
            }
            for c in charges
        ],
    }


def _days_overdue(charge) -> int:
    """How long this charge has been failing, in whole days.

    Reported so a caller can see how close a number is to the suspension and
    release thresholds without knowing the schedule in
    ``api/services/billing/dunning.py`` by heart.
    """
    from datetime import UTC, datetime

    from api.services.billing import dunning

    first_failed = charge.first_failed_at
    if first_failed is None:
        return 0
    if first_failed.tzinfo is None:
        first_failed = first_failed.replace(tzinfo=UTC)
    return dunning.days_overdue(first_failed, now=datetime.now(UTC))
