"""Who an account is, for tax purposes.

Separate from the organization because it answers a different question. The
organization is who logs in; this is who the invoice is made out to, and the
two are routinely different — an agency logs in, its holding company is billed.

The field that matters is ``state_code``. For a domestic customer it decides
CGST+SGST versus IGST, so getting it wrong produces the right total split the
wrong way — which is a filing correction rather than a display bug, and one
neither side notices until somebody reconciles a return.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import BillingProfileModel
from api.services.billing.tax import (
    TaxError,
    is_export,
    state_code_from_gstin,
    validate_gstin,
)


@dataclass(frozen=True)
class BillingProfile:
    """A profile as a screen and an invoice both see it."""

    legal_name: str | None
    gstin: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state_code: str | None
    postal_code: str | None
    country_code: str
    billing_email: str | None
    #: True when everything an invoice needs is present. Surfaced so the screen
    #: can say what is missing before the first payment rather than after.
    is_complete: bool
    is_export: bool

    def as_snapshot(self) -> dict:
        """Frozen onto a document at issue.

        A document is a statement about a moment. Rendering it from the live
        profile would rewrite every past invoice the first time a customer moves
        office.
        """
        data = asdict(self)
        data.pop("is_complete", None)
        return data


def _view(row: BillingProfileModel | None) -> BillingProfile:
    if row is None:
        return BillingProfile(
            legal_name=None,
            gstin=None,
            address_line1=None,
            address_line2=None,
            city=None,
            state_code=None,
            postal_code=None,
            country_code="IN",
            billing_email=None,
            is_complete=False,
            is_export=False,
        )

    country = (row.country_code or "IN").upper()
    export = is_export(country)
    # A domestic customer needs a state before we can decide which tax applies;
    # an export does not, because there is only one answer for them.
    complete = bool(row.legal_name and row.address_line1 and (export or row.state_code))

    return BillingProfile(
        legal_name=row.legal_name,
        gstin=row.gstin,
        address_line1=row.address_line1,
        address_line2=row.address_line2,
        city=row.city,
        state_code=row.state_code,
        postal_code=row.postal_code,
        country_code=country,
        billing_email=row.billing_email,
        is_complete=complete,
        is_export=export,
    )


async def get_profile(session: AsyncSession, *, organization_id: int) -> BillingProfile:
    """This account's profile, or an empty one.

    Never raises for a missing profile. An account that has not filled it in yet
    is the normal state of every account on its first day, and callers that only
    need the country and state should not have to branch on existence.
    """
    row = await session.scalar(
        select(BillingProfileModel).where(
            BillingProfileModel.organization_id == organization_id
        )
    )
    return _view(row)


async def save_profile(
    session: AsyncSession,
    *,
    organization_id: int,
    legal_name: str | None = None,
    gstin: str | None = None,
    address_line1: str | None = None,
    address_line2: str | None = None,
    city: str | None = None,
    state_code: str | None = None,
    postal_code: str | None = None,
    country_code: str = "IN",
    billing_email: str | None = None,
) -> BillingProfile:
    """Create or replace an account's billing profile.

    Replaces wholesale rather than patching field by field: a partial update
    that leaves a stale state code beside a new GSTIN is exactly the
    inconsistency the validation below exists to prevent.
    """
    country = (country_code or "IN").strip().upper()
    if len(country) != 2 or not country.isalpha():
        raise TaxError("Country must be a two-letter code, for example IN.")

    state = (state_code or "").strip()
    if state:
        state = state.zfill(2)
        if not state.isdigit() or len(state) != 2:
            raise TaxError("State must be a two-digit GST state code, for example 29.")

    # Validated together. A GSTIN carries its own state code, and if the two
    # disagree one of them is wrong — silently trusting either would change
    # which tax the customer is charged.
    gstin_value = validate_gstin(gstin, state_code=state or None)

    if gstin_value and is_export(country):
        raise TaxError(
            "A GSTIN is an Indian registration. Set the country to IN, or "
            "remove the GSTIN."
        )

    # A GSTIN with no state given supplies the state itself, which is better
    # than storing a registration whose state we then have to guess at.
    if gstin_value and not state:
        state = state_code_from_gstin(gstin_value) or ""

    row = await session.scalar(
        select(BillingProfileModel).where(
            BillingProfileModel.organization_id == organization_id
        )
    )
    if row is None:
        row = BillingProfileModel(organization_id=organization_id)
        session.add(row)

    row.legal_name = (legal_name or "").strip() or None
    row.gstin = gstin_value
    row.address_line1 = (address_line1 or "").strip() or None
    row.address_line2 = (address_line2 or "").strip() or None
    row.city = (city or "").strip() or None
    row.state_code = state or None
    row.postal_code = (postal_code or "").strip() or None
    row.country_code = country
    row.billing_email = (billing_email or "").strip() or None

    await session.flush()
    logger.info(
        "Billing profile saved for org {} ({}, state {})",
        organization_id,
        country,
        state or "unset",
    )
    return _view(row)
