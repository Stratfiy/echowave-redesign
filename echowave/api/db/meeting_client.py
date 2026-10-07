"""Reads and writes for meeting records (launch stream `meetings`).

Every read of a meeting a person asked for names both the workspace and the
person (``organization_id`` and ``owner_user_id``): a meeting is private to
whoever captured it, even from colleagues in the same workspace. The worker,
which acts on a meeting by its own row id, still names the workspace.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from api.db.base_client import BaseDBClient
from api.db.meeting_models import (
    MeetingBreakModel,
    MeetingItemModel,
    MeetingModel,
    MeetingSegmentModel,
)


class MeetingClient(BaseDBClient):
    # --- meetings ---------------------------------------------------------

    async def create_meeting(self, **fields: Any) -> MeetingModel:
        async with self.async_session() as session:
            meeting = MeetingModel(**fields)
            session.add(meeting)
            await session.commit()
            await session.refresh(meeting)
            return meeting

    async def get_meeting(
        self, public_id: str, *, organization_id: int, owner_user_id: int
    ) -> MeetingModel | None:
        """One person's meeting, or None -- for a wrong id, a wrong
        workspace and a colleague's meeting alike."""
        async with self.async_session() as session:
            return await session.scalar(
                select(MeetingModel).where(
                    MeetingModel.public_id == public_id,
                    MeetingModel.organization_id == organization_id,
                    MeetingModel.owner_user_id == owner_user_id,
                )
            )

    async def get_meeting_by_id(
        self, meeting_id: int, *, organization_id: int
    ) -> MeetingModel | None:
        """For the worker, which was handed the row id by the route."""
        async with self.async_session() as session:
            return await session.scalar(
                select(MeetingModel).where(
                    MeetingModel.id == meeting_id,
                    MeetingModel.organization_id == organization_id,
                )
            )

    async def list_meetings(
        self, *, organization_id: int, owner_user_id: int, limit: int = 50
    ) -> Sequence[MeetingModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(MeetingModel)
                .where(
                    MeetingModel.organization_id == organization_id,
                    MeetingModel.owner_user_id == owner_user_id,
                )
                .order_by(MeetingModel.created_at.desc(), MeetingModel.id.desc())
                .limit(max(1, min(limit, 200)))
            )
            return list(result.scalars().all())

    async def update_meeting(
        self,
        meeting_id: int,
        *,
        organization_id: int,
        only_if_status: Sequence[str] = (),
        **fields: Any,
    ) -> MeetingModel | None:
        """Change fields and bump the revision. With ``only_if_status`` the
        change is conditional (compare-and-swap on the status): None when the
        meeting had already moved on."""
        values = dict(fields)
        values["updated_at"] = datetime.now(UTC)
        values["revision"] = MeetingModel.revision + 1
        stmt = (
            update(MeetingModel)
            .where(
                MeetingModel.id == meeting_id,
                MeetingModel.organization_id == organization_id,
            )
            .values(**values)
            .returning(MeetingModel)
        )
        if only_if_status:
            stmt = stmt.where(MeetingModel.status.in_(list(only_if_status)))
        async with self.async_session() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            await session.commit()
            if row is not None:
                await session.refresh(row)
            return row

    async def delete_meeting(self, meeting_id: int, *, organization_id: int) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                delete(MeetingModel).where(
                    MeetingModel.id == meeting_id,
                    MeetingModel.organization_id == organization_id,
                )
            )
            await session.commit()
            return bool(result.rowcount)

    # --- segments ---------------------------------------------------------

    async def add_meeting_segment(
        self, *, meeting_id: int, organization_id: int, seq: int, **fields: Any
    ) -> tuple[MeetingSegmentModel, bool]:
        """One segment. A second upload of the same number (a client retry
        after a dropped response) returns the first and False."""
        stmt = (
            insert(MeetingSegmentModel)
            .values(
                meeting_id=meeting_id,
                organization_id=organization_id,
                seq=seq,
                created_at=datetime.now(UTC),
                **fields,
            )
            .on_conflict_do_nothing(constraint="uq_meeting_segments_seq")
            .returning(MeetingSegmentModel.id)
        )
        async with self.async_session() as session:
            new_id = (await session.execute(stmt)).scalar_one_or_none()
            await session.commit()
            row = await session.scalar(
                select(MeetingSegmentModel).where(
                    MeetingSegmentModel.meeting_id == meeting_id,
                    MeetingSegmentModel.organization_id == organization_id,
                    MeetingSegmentModel.seq == seq,
                )
            )
            return row, new_id is not None

    async def meeting_segments(
        self, meeting_id: int, *, organization_id: int, with_audio: bool = False
    ) -> list[MeetingSegmentModel]:
        query = select(MeetingSegmentModel).where(
            MeetingSegmentModel.meeting_id == meeting_id,
            MeetingSegmentModel.organization_id == organization_id,
        )
        if not with_audio:
            from sqlalchemy.orm import defer

            query = query.options(defer(MeetingSegmentModel.audio))
        async with self.async_session() as session:
            result = await session.execute(query.order_by(MeetingSegmentModel.seq))
            return list(result.scalars().all())

    async def claim_meeting_segment(
        self, segment_id: int, *, organization_id: int
    ) -> MeetingSegmentModel | None:
        """Move a pending segment to transcribing, if nobody else has. The
        winner gets the row with its audio; everyone else gets None."""
        async with self.async_session() as session:
            row = (
                await session.execute(
                    update(MeetingSegmentModel)
                    .where(
                        MeetingSegmentModel.id == segment_id,
                        MeetingSegmentModel.organization_id == organization_id,
                        MeetingSegmentModel.status == "pending",
                    )
                    .values(status="transcribing")
                    .returning(MeetingSegmentModel)
                )
            ).scalar_one_or_none()
            await session.commit()
            if row is not None:
                await session.refresh(row)
            return row

    async def update_meeting_segment(
        self, segment_id: int, *, organization_id: int, **fields: Any
    ) -> None:
        async with self.async_session() as session:
            await session.execute(
                update(MeetingSegmentModel)
                .where(
                    MeetingSegmentModel.id == segment_id,
                    MeetingSegmentModel.organization_id == organization_id,
                )
                .values(**fields)
            )
            await session.commit()

    async def delete_meeting_segment(
        self, segment_id: int, *, organization_id: int
    ) -> None:
        async with self.async_session() as session:
            await session.execute(
                delete(MeetingSegmentModel).where(
                    MeetingSegmentModel.id == segment_id,
                    MeetingSegmentModel.organization_id == organization_id,
                )
            )
            await session.commit()

    async def meeting_language_for_segment(
        self, segment_id: int, *, organization_id: int
    ) -> str | None:
        """The language of the meeting a segment belongs to, or None."""
        async with self.async_session() as session:
            return await session.scalar(
                select(MeetingModel.language)
                .join(
                    MeetingSegmentModel,
                    MeetingSegmentModel.meeting_id == MeetingModel.id,
                )
                .where(
                    MeetingSegmentModel.id == segment_id,
                    MeetingSegmentModel.organization_id == organization_id,
                    MeetingModel.organization_id == organization_id,
                )
            )

    async def next_meeting_seq(self, meeting_id: int, *, organization_id: int) -> int:
        async with self.async_session() as session:
            highest = await session.scalar(
                select(func.max(MeetingSegmentModel.seq)).where(
                    MeetingSegmentModel.meeting_id == meeting_id,
                    MeetingSegmentModel.organization_id == organization_id,
                )
            )
            return int(highest) + 1 if highest is not None else 0

    # --- pauses and gaps -----------------------------------------------------

    async def add_meeting_break(
        self, *, meeting_id: int, organization_id: int, **fields: Any
    ) -> MeetingBreakModel:
        async with self.async_session() as session:
            row = MeetingBreakModel(
                meeting_id=meeting_id, organization_id=organization_id, **fields
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def meeting_breaks(
        self, meeting_id: int, *, organization_id: int
    ) -> list[MeetingBreakModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(MeetingBreakModel)
                .where(
                    MeetingBreakModel.meeting_id == meeting_id,
                    MeetingBreakModel.organization_id == organization_id,
                )
                .order_by(MeetingBreakModel.at_ms, MeetingBreakModel.id)
            )
            return list(result.scalars().all())

    # --- decisions and suggested actions ---------------------------------------

    async def meeting_items(
        self, meeting_id: int, *, organization_id: int, kind: str | None = None
    ) -> list[MeetingItemModel]:
        query = select(MeetingItemModel).where(
            MeetingItemModel.meeting_id == meeting_id,
            MeetingItemModel.organization_id == organization_id,
        )
        if kind is not None:
            query = query.where(MeetingItemModel.kind == kind)
        async with self.async_session() as session:
            result = await session.execute(
                query.order_by(MeetingItemModel.position, MeetingItemModel.id)
            )
            return list(result.scalars().all())

    async def replace_meeting_suggestions(
        self,
        meeting_id: int,
        *,
        organization_id: int,
        items: list[dict[str, Any]],
    ) -> None:
        """Put a fresh reading's items in place of the last one's -- except
        those a person has touched: an action with a card or a task, or one
        they edited, stays exactly as it is."""
        async with self.async_session() as session:
            await session.execute(
                delete(MeetingItemModel).where(
                    MeetingItemModel.meeting_id == meeting_id,
                    MeetingItemModel.organization_id == organization_id,
                    MeetingItemModel.card_event_id.is_(None),
                    MeetingItemModel.task_id.is_(None),
                    MeetingItemModel.edited.is_(False),
                )
            )
            now = datetime.now(UTC)
            for item in items:
                session.add(
                    MeetingItemModel(
                        meeting_id=meeting_id,
                        organization_id=organization_id,
                        created_at=now,
                        updated_at=now,
                        **item,
                    )
                )
            await session.commit()

    async def get_meeting_item(
        self, item_id: int, *, meeting_id: int, organization_id: int
    ) -> MeetingItemModel | None:
        async with self.async_session() as session:
            return await session.scalar(
                select(MeetingItemModel).where(
                    MeetingItemModel.id == item_id,
                    MeetingItemModel.meeting_id == meeting_id,
                    MeetingItemModel.organization_id == organization_id,
                )
            )

    async def update_meeting_item(
        self, item_id: int, *, meeting_id: int, organization_id: int, **fields: Any
    ) -> MeetingItemModel | None:
        values = dict(fields)
        values["updated_at"] = datetime.now(UTC)
        async with self.async_session() as session:
            row = (
                await session.execute(
                    update(MeetingItemModel)
                    .where(
                        MeetingItemModel.id == item_id,
                        MeetingItemModel.meeting_id == meeting_id,
                        MeetingItemModel.organization_id == organization_id,
                    )
                    .values(**values)
                    .returning(MeetingItemModel)
                )
            ).scalar_one_or_none()
            await session.commit()
            if row is not None:
                await session.refresh(row)
            return row
