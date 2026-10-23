"""Storage for the payment links agents created, and their watch."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.db.base_client import BaseDBClient
from api.db.models import PaymentLinkModel


class PaymentLinkClient(BaseDBClient):
    async def record_payment_link(self, **values: Any) -> PaymentLinkModel | None:
        """Store a created link once; a repeat of the same link is a no-op."""
        async with self.async_session() as session:
            statement = (
                pg_insert(PaymentLinkModel)
                .values(**values)
                .on_conflict_do_nothing(constraint="uq_payment_links_link")
                .returning(PaymentLinkModel)
            )
            row = (await session.execute(statement)).scalar_one_or_none()
            await session.commit()
            return row

    async def payment_links_to_check(
        self, *, created_after: datetime, checked_before: datetime, limit: int
    ) -> list[PaymentLinkModel]:
        """Open links young enough to watch and not checked just now, across
        every workspace: the watch is one job for the platform."""
        async with self.async_session() as session:
            result = await session.execute(
                select(PaymentLinkModel)
                .where(
                    PaymentLinkModel.status == "created",
                    PaymentLinkModel.created_at >= created_after,
                    or_(
                        PaymentLinkModel.checked_at.is_(None),
                        PaymentLinkModel.checked_at < checked_before,
                    ),
                )
                .order_by(PaymentLinkModel.checked_at.asc().nulls_first())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def update_payment_link(
        self, link_row_id: int, *, organization_id: int, **values: Any
    ) -> None:
        async with self.async_session() as session:
            await session.execute(
                update(PaymentLinkModel)
                .where(
                    PaymentLinkModel.id == link_row_id,
                    PaymentLinkModel.organization_id == organization_id,
                )
                .values(**values)
            )
            await session.commit()

    async def expire_payment_links(self, *, created_before: datetime) -> int:
        """Stop watching links nobody paid in time."""
        async with self.async_session() as session:
            result = await session.execute(
                update(PaymentLinkModel)
                .where(
                    PaymentLinkModel.status == "created",
                    PaymentLinkModel.created_at < created_before,
                )
                .values(status="expired")
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def payment_links_for_organization(
        self, organization_id: int, *, limit: int = 50
    ) -> list[PaymentLinkModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(PaymentLinkModel)
                .where(PaymentLinkModel.organization_id == organization_id)
                .order_by(PaymentLinkModel.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())
