"""Accounts whose margin has fallen under the floor, for staff.

Margin by account is on the superadmin dashboard and nobody watches a
dashboard. This is the alarm: over the last three days, an account that
spent enough to matter and left us less than the floor of it is a row
staff see each morning, and one mail when it first appears. A thin
margin is usually one of three things — a model with no rate, a
negotiated platform rate that no longer covers the stack, or own-key
usage still carrying a fee — and all three are staff work, not the
customer's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import MARGIN_FLOOR_BPS, MARGIN_WATCH_MIN_CHARGED_PAISE
from api.db.models import DailyOrganizationRollupModel, OrganizationModel

WINDOW_DAYS = 3


@dataclass(frozen=True)
class ThinAccount:
    organization_id: int
    name: str
    charged_paise: int
    provider_cost_paise: int

    @property
    def margin_bps(self) -> int:
        if self.charged_paise <= 0:
            return 0
        return (
            (self.charged_paise - self.provider_cost_paise)
            * 10_000
            // self.charged_paise
        )

    def as_dict(self) -> dict:
        return {
            "organization_id": self.organization_id,
            "name": self.name,
            "charged_paise": self.charged_paise,
            "provider_cost_paise": self.provider_cost_paise,
            "margin_bps": self.margin_bps,
        }


def is_thin(
    *,
    charged_paise: int,
    provider_cost_paise: int,
    floor_bps: int = MARGIN_FLOOR_BPS,
    min_charged: int = MARGIN_WATCH_MIN_CHARGED_PAISE,
) -> bool:
    """Spent enough to matter, and left us under the floor of it."""
    if charged_paise < min_charged:
        return False
    return (charged_paise - provider_cost_paise) * 10_000 < floor_bps * charged_paise


async def thin_accounts(
    session: AsyncSession, *, now: datetime | None = None
) -> list[ThinAccount]:
    now = now or datetime.now(UTC)
    since: date = (now - timedelta(days=WINDOW_DAYS - 1)).date()
    rows = (
        await session.execute(
            select(
                DailyOrganizationRollupModel.organization_id,
                func.coalesce(func.sum(DailyOrganizationRollupModel.charged_paise), 0),
                func.coalesce(
                    func.sum(DailyOrganizationRollupModel.provider_cost_paise), 0
                ),
                OrganizationModel.billing_name,
                OrganizationModel.provider_id,
            )
            .join(
                OrganizationModel,
                OrganizationModel.id == DailyOrganizationRollupModel.organization_id,
            )
            .where(DailyOrganizationRollupModel.day >= since)
            .group_by(
                DailyOrganizationRollupModel.organization_id,
                OrganizationModel.billing_name,
                OrganizationModel.provider_id,
            )
        )
    ).all()
    out = []
    for organization_id, charged, cost, billing_name, provider_id in rows:
        charged, cost = int(charged or 0), int(cost or 0)
        if is_thin(charged_paise=charged, provider_cost_paise=cost):
            out.append(
                ThinAccount(
                    organization_id=int(organization_id),
                    name=billing_name or provider_id or f"Account {organization_id}",
                    charged_paise=charged,
                    provider_cost_paise=cost,
                )
            )
    out.sort(key=lambda a: a.margin_bps)
    return out
