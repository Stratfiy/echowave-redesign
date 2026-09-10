"""Tell staff, once a day per model, that a call was settled with no rate.

An unpriced line is charged to the customer at zero and reported as zero
provider cost, so margin reads higher than it is and the customer is billed
less than the vendor bills us. The superadmin page shows it; nobody opens
that page at the moment it starts happening. One mail per model per day,
to every superadmin, is the alarm.

**Never raises.** Settlement has already costed the call; a mail server
having a bad minute must not make it fail.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.constants import UI_APP_URL
from api.db import db_client
from api.db.models import NotificationModel, UserModel
from api.enums import StaffRole
from api.services.messaging import email

KIND = "uncosted_usage"


async def superadmin_addresses() -> list[str]:
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                select(UserModel.email).where(
                    UserModel.staff_role == StaffRole.SUPERADMIN.value
                )
            )
        ).scalars()
    return sorted({(e or "").strip().lower() for e in rows if (e or "").strip()})


def compose(*, organization_id: int, workflow_run_id: int, labels: list[str]):
    base = (UI_APP_URL or "").rstrip("/")
    return (
        f"No rate on file: {', '.join(labels)}",
        (
            f"Workflow run {workflow_run_id} on organization {organization_id} "
            f"used {len(labels)} model(s) with no rate on file:\n\n  "
            + "\n  ".join(labels)
            + "\n\nThe customer was charged nothing for those lines and the "
            "provider cost is recorded as zero, so margin for this account is "
            "overstated until a rate is added. Add it under the catalogue and "
            f"recost the run:\n  {base}/superadmin/billing/uncosted\n\n"
            "This is the first such run today for these models; later ones "
            "are on that page."
        ),
    )


async def notify(
    *, organization_id: int, workflow_run_id: int, labels: list[str]
) -> bool:
    """Mail staff about ``labels`` unless they were told today. Returns whether sent."""
    if not labels:
        return False
    try:
        day = datetime.now(UTC).date().isoformat()
        dedupe = f"{day}:" + ",".join(sorted(labels))
        recipients = await superadmin_addresses()
        if not recipients or not email.email_is_configured():
            logger.warning(
                "Uncosted usage on run {} ({}) but no staff address or SMTP to tell",
                workflow_run_id,
                ", ".join(labels),
            )
            return False
        subject, body = compose(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            labels=labels,
        )
        async with db_client.async_session() as session:
            record = NotificationModel(
                organization_id=organization_id,
                kind=KIND,
                dedupe_key=dedupe[:128],
                channel="email",
                recipients=", ".join(recipients),
                subject=subject,
            )
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False
        results = [
            await email.send_email(
                sender="billing", to=address, subject=subject, body_text=body
            )
            for address in recipients
        ]
        sent = any(r.ok for r in results)
        async with db_client.async_session() as session:
            stored = await session.get(NotificationModel, record.id)
            if stored is not None:
                stored.sent = sent
                if not sent:
                    stored.error = (
                        next((r.error for r in results if not r.ok and r.error), None)
                        or "SMTP send failed; see worker logs"
                    )
                await session.commit()
        return sent
    except Exception as exc:  # noqa: BLE001 — reported, never propagated
        logger.error("Could not alert staff about uncosted usage: {}", exc)
        return False
