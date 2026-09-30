"""The trial that replaces Free (PLAN-1, KAN-255).

Founder decision, 30 Sep 2026: no Free plan, only a trial. While the
``trial_plan`` feature is on for an account and it has no authorised plan
mandate, it is on the ``trial`` plan for a fixed window. After the window it
keeps read access to everything (threads, reports, exports) and new runs are
refused with a message that offers the plans. Nothing is deleted.

The window starts at the later of the account's creation and
``TRIAL_PLAN_STARTS_AT`` (launch day), so accounts that existed before the
switch get a full window from the day it went on rather than an instantly
expired one. ``organizations.trial_ends_at``, set by staff, overrides the
computed end (an extension, or a pilot's longer window).

Read at call time so tests and a flag flip need no restart of this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger

from api.services import features

FLAG = "trial_plan"
TRIAL = "trial"
ERROR_CODE = "trial_ended"

_DEFAULT_DAYS = 14
_DEFAULT_STARTS_AT = "2026-10-04T00:00:00+05:30"


def trial_days() -> int:
    try:
        return max(1, int(os.getenv("TRIAL_DAYS", str(_DEFAULT_DAYS))))
    except ValueError:
        return _DEFAULT_DAYS


def starts_at_floor() -> datetime:
    raw = os.getenv("TRIAL_PLAN_STARTS_AT", _DEFAULT_STARTS_AT)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        logger.warning(
            "TRIAL_PLAN_STARTS_AT {!r} is not ISO 8601; using launch day", raw
        )
        parsed = datetime.fromisoformat(_DEFAULT_STARTS_AT)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def applies(organization_id: int | None) -> bool:
    return features.is_on(FLAG, organization_id)


@dataclass(frozen=True)
class TrialStatus:
    #: The account is on the trial (flag on, no authorised plan mandate).
    on_trial: bool
    #: Inside the window. False once it has ended.
    active: bool
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    @property
    def days_left(self) -> int | None:
        if self.ends_at is None:
            return None
        remaining = self.ends_at - datetime.now(UTC)
        return max(0, remaining.days + (1 if remaining.seconds > 0 else 0))

    def as_dict(self) -> dict:
        return {
            "on_trial": self.on_trial,
            "active": self.active,
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "ends_at": self.ends_at.isoformat() if self.ends_at else None,
            "days_left": self.days_left,
            "days": trial_days(),
        }


NOT_ON_TRIAL = TrialStatus(on_trial=False, active=False)


def window(
    *, created_at: datetime | None, override_ends_at: datetime | None
) -> tuple[datetime, datetime]:
    floor = starts_at_floor()
    created = created_at or floor
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    starts = max(created, floor)
    if override_ends_at is not None:
        ends = (
            override_ends_at
            if override_ends_at.tzinfo
            else override_ends_at.replace(tzinfo=UTC)
        )
    else:
        ends = starts + timedelta(days=trial_days())
    return starts, ends


async def status(session, *, organization_id: int) -> TrialStatus:
    """Where this account stands. Never raises: an account whose status
    cannot be read is treated as not on trial, which is the pre-trial
    behaviour (credit and plan checks still apply)."""
    if not applies(organization_id):
        return NOT_ON_TRIAL
    try:
        from api.db.models import OrganizationModel
        from api.services.billing.mandates import (
            PURPOSE_STARTER_PLAN,
            get_mandate,
            is_authorised,
        )

        mandate = await get_mandate(
            session, organization_id=organization_id, purpose=PURPOSE_STARTER_PLAN
        )
        if is_authorised(mandate):
            return NOT_ON_TRIAL
        org = await session.get(OrganizationModel, organization_id)
        if org is None:
            return NOT_ON_TRIAL
        starts, ends = window(
            created_at=org.created_at,
            override_ends_at=getattr(org, "trial_ends_at", None),
        )
        return TrialStatus(
            on_trial=True,
            active=datetime.now(UTC) < ends,
            starts_at=starts,
            ends_at=ends,
        )
    except Exception:
        logger.exception("Could not read trial status for org {}", organization_id)
        return NOT_ON_TRIAL


async def status_in_own_session(*, organization_id: int) -> TrialStatus:
    from api.db import db_client

    async with db_client.async_session() as session:
        return await status(session, organization_id=organization_id)


def ended_message(ends_at: datetime | None) -> str:
    when = ends_at.strftime("%-d %B") if ends_at else "recently"
    return (
        f"Your Decibyl trial ended on {when}. Your agents, threads and reports "
        "are all still here. Choose a plan in Billing to switch them back on."
    )
