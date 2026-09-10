"""How much to top up, said at the moment the balance is shown low.

"Running low" is a feeling; "top up 1,400 credits to cover next week" is
a decision. The number is what a week costs at this account's recent
burn, less what is left, rounded up to a top-up step and never below the
minimum. None when the account is not low, or has no burn to measure.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import MIN_TOPUP_PAISE, TOPUP_INCREMENT_PAISE
from api.db.models import DailyOrganizationRollupModel

BURN_WINDOW_DAYS = 7
COVER_DAYS = 7
#: The chip calls it low at five floors; the nudge appears at the same line.
LOW_MULTIPLE_OF_FLOOR = 5


def suggest(
    *,
    balance_paise: int,
    daily_burn_paise: int,
    min_balance_paise: int,
    min_topup_paise: int = MIN_TOPUP_PAISE,
    increment_paise: int = TOPUP_INCREMENT_PAISE,
) -> int | None:
    """Pure: the amount to suggest, or None when there is nothing to say."""
    if daily_burn_paise <= 0:
        return None
    if balance_paise > min_balance_paise * LOW_MULTIPLE_OF_FLOOR:
        return None
    needed = daily_burn_paise * COVER_DAYS - max(balance_paise, 0)
    amount = max(needed, min_topup_paise)
    step = max(1, increment_paise)
    return ((amount + step - 1) // step) * step


async def daily_burn_paise(session: AsyncSession, *, organization_id: int) -> int:
    since = (datetime.now(UTC) - timedelta(days=BURN_WINDOW_DAYS - 1)).date()
    charged = await session.scalar(
        select(
            func.coalesce(func.sum(DailyOrganizationRollupModel.charged_paise), 0)
        ).where(
            DailyOrganizationRollupModel.organization_id == organization_id,
            DailyOrganizationRollupModel.day >= since,
        )
    )
    return int(charged or 0) // BURN_WINDOW_DAYS
