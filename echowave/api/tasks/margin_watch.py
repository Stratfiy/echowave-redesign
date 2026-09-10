"""Each morning, tell staff which accounts left us under the margin floor."""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.exc import IntegrityError

from api.constants import MARGIN_FLOOR_BPS, UI_APP_URL
from api.db import db_client
from api.db.models import NotificationModel
from api.services.billing import margin_watch
from api.services.billing.uncosted_alert import superadmin_addresses
from api.services.messaging import email

KIND = "margin_watch"


def compose(account: margin_watch.ThinAccount) -> tuple[str, str]:
    base = (UI_APP_URL or "").rstrip("/")
    pct = account.margin_bps / 100
    return (
        f"Margin under floor: {account.name} at {pct:.0f}%",
        (
            f"{account.name} (organization {account.organization_id}) spent "
            f"{account.charged_paise / 100:,.0f} credits over the last "
            f"{margin_watch.WINDOW_DAYS} days and our vendor cost was "
            f"{account.provider_cost_paise / 100:,.0f}, a margin of {pct:.0f}% "
            f"against a floor of {MARGIN_FLOOR_BPS / 100:.0f}%.\n\n"
            "Usually one of: a model with no rate on file, a negotiated platform "
            "rate that no longer covers the stack, or own-key usage still "
            f"carrying a fee.\n\n  {base}/superadmin/billing/accounts/{account.organization_id}\n"
        ),
    )


async def watch_margins(ctx=None, *, now: datetime | None = None) -> dict:
    """Mail staff once per thin account per day. Safe to re-run."""
    now = now or datetime.now(UTC)
    counters = {"thin": 0, "sent": 0, "skipped": 0}
    async with db_client.async_session() as session:
        thin = await margin_watch.thin_accounts(session, now=now)
    counters["thin"] = len(thin)
    if not thin:
        return counters
    recipients = await superadmin_addresses()
    if not recipients or not email.email_is_configured():
        logger.warning(
            "{} accounts under the margin floor and nobody to tell", len(thin)
        )
        return counters
    day = now.date().isoformat()
    for account in thin:
        subject, body = compose(account)
        async with db_client.async_session() as session:
            record = NotificationModel(
                organization_id=account.organization_id,
                kind=KIND,
                dedupe_key=day,
                channel="email",
                recipients=", ".join(recipients),
                subject=subject,
            )
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                counters["skipped"] += 1
                continue
        results = [
            await email.send_email(
                sender="billing", to=address, subject=subject, body_text=body
            )
            for address in recipients
        ]
        if any(r.ok for r in results):
            counters["sent"] += 1
    logger.info(
        "Margin watch: {thin} thin, {sent} sent, {skipped} already told", **counters
    )
    return counters
