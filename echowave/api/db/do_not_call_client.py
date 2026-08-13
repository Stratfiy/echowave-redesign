from typing import Iterable, Optional, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.db.base_client import BaseDBClient
from api.db.models import DoNotCallEntryModel


class DoNotCallClient(BaseDBClient):
    """Reads and writes for the per-organization do-not-disturb list.

    Every number that crosses this boundary is expected to be already
    normalised by services/compliance/dnd.normalise_number. Normalising here
    as well would put the rule in two places, and the failure mode of the two
    disagreeing is a list that silently stops matching.
    """

    async def is_number_dnd_listed(
        self, organization_id: int, phone_number: str
    ) -> bool:
        """The dialler's question, asked once per call.

        Deliberately a bare EXISTS on the covering index rather than fetching
        the row: nothing on the hot path needs the note or the provenance.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(DoNotCallEntryModel.id)
                .where(
                    DoNotCallEntryModel.organization_id == organization_id,
                    DoNotCallEntryModel.phone_number == phone_number,
                )
                .limit(1)
            )
            return result.scalars().first() is not None

    async def add_dnd_entries(
        self,
        organization_id: int,
        phone_numbers: Iterable[str],
        *,
        source: str = "manual",
        note: Optional[str] = None,
        created_by: Optional[int] = None,
    ) -> int:
        """Add numbers, ignoring ones already listed. Returns how many were new.

        ON CONFLICT DO NOTHING rather than a read-then-write: re-uploading last
        month's file is the normal case, not the exception, and a check-then-
        insert races itself when two uploads overlap.
        """
        rows = [
            {
                "organization_id": organization_id,
                "phone_number": number,
                "source": source,
                "note": note,
                "created_by": created_by,
            }
            for number in dict.fromkeys(n for n in phone_numbers if n)
        ]
        if not rows:
            return 0

        async with self.async_session() as session:
            statement = (
                pg_insert(DoNotCallEntryModel)
                .values(rows)
                .on_conflict_do_nothing(constraint="_dnc_org_number_uc")
                .returning(DoNotCallEntryModel.id)
            )
            result = await session.execute(statement)
            inserted = len(result.scalars().all())
            await session.commit()
            return inserted

    async def remove_dnd_entry(self, organization_id: int, phone_number: str) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                delete(DoNotCallEntryModel).where(
                    DoNotCallEntryModel.organization_id == organization_id,
                    DoNotCallEntryModel.phone_number == phone_number,
                )
            )
            await session.commit()
            return result.rowcount > 0

    async def list_dnd_entries(
        self,
        organization_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[DoNotCallEntryModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(DoNotCallEntryModel)
                .where(DoNotCallEntryModel.organization_id == organization_id)
                .order_by(DoNotCallEntryModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return result.scalars().all()

    async def count_dnd_entries(self, organization_id: int) -> int:
        async with self.async_session() as session:
            result = await session.execute(
                select(func.count(DoNotCallEntryModel.id)).where(
                    DoNotCallEntryModel.organization_id == organization_id
                )
            )
            return result.scalar_one()
