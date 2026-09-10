"""The inbox behind the bell.

Every notice that matters to an account — low credit, a failed charge, a
scheduled top-up, credits landing — went out by email and nowhere else.
Email is the right channel for money, and the wrong only channel: a person
sitting in the product with a warning in an inbox they check twice a day is
a person who finds out at the moment a call is refused.

**Posting never raises.** Every caller has already done the thing being
announced, and a full inbox table must not unwind it. The same
``(organization, kind, dedupe_key)`` rule as the email log means a job that
is safe to re-run posts once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from api.db import db_client
from api.db.models import InAppNotificationModel

#: Kept short in the bell; the email carries the detail.
BODY_LIMIT = 400


@dataclass(frozen=True)
class Item:
    id: int
    kind: str
    title: str
    body: str | None
    link: str | None
    created_at: datetime
    read_at: datetime | None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "link": self.link,
            "created_at": self.created_at.isoformat(),
            "read_at": self.read_at.isoformat() if self.read_at else None,
        }


def _brief(text: str | None) -> str | None:
    """The first paragraph, trimmed: the email's opening line is the summary."""
    if not text:
        return None
    first = text.strip().split("\n\n", 1)[0].strip()
    return first[:BODY_LIMIT]


async def post(
    *,
    organization_id: int,
    kind: str,
    dedupe_key: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
) -> bool:
    """Put one item in the account's inbox. Returns whether it was new."""
    try:
        async with db_client.async_session() as session:
            # Checked first, as the signup bonus does: the unique index is for
            # the race, not the ordinary repeat. A rollback is also the one
            # thing a shared test connection cannot absorb quietly.
            existing = await session.scalar(
                select(InAppNotificationModel.id).where(
                    InAppNotificationModel.organization_id == organization_id,
                    InAppNotificationModel.kind == kind,
                    InAppNotificationModel.dedupe_key == dedupe_key[:128],
                )
            )
            if existing is not None:
                return False
            session.add(
                InAppNotificationModel(
                    organization_id=organization_id,
                    kind=kind,
                    dedupe_key=dedupe_key[:128],
                    title=title[:500],
                    body=_brief(body),
                    link=link,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False
        return True
    except Exception as exc:  # noqa: BLE001 — reported, never propagated
        logger.error(
            "Could not post a {} notification to organization {}: {}",
            kind,
            organization_id,
            exc,
        )
        return False


async def list_items(*, organization_id: int, limit: int = 30) -> list[Item]:
    async with db_client.async_session() as session:
        rows = (
            (
                await session.execute(
                    select(InAppNotificationModel)
                    .where(InAppNotificationModel.organization_id == organization_id)
                    .order_by(InAppNotificationModel.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [
        Item(
            id=row.id,
            kind=row.kind,
            title=row.title,
            body=row.body,
            link=row.link,
            created_at=row.created_at,
            read_at=row.read_at,
        )
        for row in rows
    ]


async def unread_count(*, organization_id: int) -> int:
    async with db_client.async_session() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(InAppNotificationModel)
                .where(
                    InAppNotificationModel.organization_id == organization_id,
                    InAppNotificationModel.read_at.is_(None),
                )
            )
            or 0
        )


async def mark_read(
    *, organization_id: int, ids: list[int] | None = None, now: datetime | None = None
) -> int:
    """Mark ``ids`` read, or everything when ``ids`` is None. Returns how many."""
    moment = now or datetime.now(UTC)
    statement = (
        update(InAppNotificationModel)
        .where(
            InAppNotificationModel.organization_id == organization_id,
            InAppNotificationModel.read_at.is_(None),
        )
        .values(read_at=moment)
    )
    if ids is not None:
        statement = statement.where(InAppNotificationModel.id.in_(ids))
    async with db_client.async_session() as session:
        result = await session.execute(statement)
        await session.commit()
        return int(result.rowcount or 0)
