"""Scheduled check that the accounts behind our keys can still pay.

Sibling of ``credential_health``, and it exists because that job cannot see
this failure. A revoked key gets rejected and caught there. An *exhausted* key
is not rejected by anything — it authenticates, it passes every probe in
``check_validity``, and it fails for the first time on a real call. See
``services/configuration/provider_balance.py``.

Hourly, offset from the credential sweep so the two are not asking the same
vendors in the same minute. Not at startup: a deployment restart is not new
information about a balance, and four vendor round trips on every boot would
make a rolling restart look like a burst of traffic to accounts that rate-limit.

Loud only where it is actionable. ``low`` and ``empty`` are logged for someone
to act on; a vendor we could not reach is logged at debug, because a provider
having a bad minute is not evidence about our balance and treating it as one
would fire an alert every time a status page went yellow.
"""

from loguru import logger

from api.db import db_client
from api.services.configuration.provider_balance import ProviderBalance, read_all


async def check_provider_balances(_ctx) -> None:
    """Read every provider balance and log the ones that need a top-up."""
    try:
        async with db_client.async_session() as session:
            balances = await read_all(session)
    except Exception as error:  # noqa: BLE001 - a cron must not die on one bad tick
        logger.error("Provider balance check failed: {}", error)
        return

    for balance in balances:
        if balance.status == "empty":
            logger.error(
                "Decibyl's {} account is empty ({}). Everything running on it "
                "is failing now — top it up.",
                balance.provider,
                _describe(balance),
            )
        elif balance.status == "low":
            logger.warning(
                "Decibyl's {} account is running low ({}). Top it up before it "
                "stops calls.",
                balance.provider,
                _describe(balance),
            )
        elif balance.status == "unreachable":
            logger.debug(
                "Could not read the {} balance: {}", balance.provider, balance.detail
            )

    read = [b for b in balances if b.status in ("ok", "low", "empty")]
    logger.info(
        "Provider balance check: {} account(s) read, {} needing attention",
        len(read),
        sum(1 for b in balances if b.needs_attention),
    )


def _describe(balance: ProviderBalance) -> str:
    """The figure itself, so the alert is actionable without opening a dashboard."""
    remaining = balance.remaining
    if remaining is None:
        return balance.detail or "no figure"
    if balance.kind == "quota":
        renews = f", renews {balance.renews_at:%d %b}" if balance.renews_at else ""
        return f"{remaining:,.0f} of {balance.limit:,.0f} left{renews}"
    currency = f" {balance.currency.upper()}" if balance.currency else ""
    return f"{remaining:,.2f}{currency} left"
