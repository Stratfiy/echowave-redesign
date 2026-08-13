from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.db.base_client import BaseDBClient
from api.db.models import VerifiedNumberModel


class VerifiedNumberClient(BaseDBClient):
    """Storage for numbers an organization has proved it can answer.

    Numbers crossing this boundary are already normalised by
    services/compliance/dnd.normalise_number. Normalising here too would put
    the rule in two places, and two copies of a normalisation rule eventually
    disagree — at which point a verified number stops matching itself.
    """

    async def get_verified_number(
        self, organization_id: int, phone_number: str
    ) -> Optional[VerifiedNumberModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(VerifiedNumberModel).where(
                    VerifiedNumberModel.organization_id == organization_id,
                    VerifiedNumberModel.phone_number == phone_number,
                )
            )
            return result.scalars().first()

    async def upsert_verified_number_challenge(
        self,
        organization_id: int,
        phone_number: str,
        *,
        code_hash: str,
        code_salt: str,
        code_expires_at: datetime,
        sent_at: datetime,
        label: Optional[str] = None,
        created_by: Optional[int] = None,
    ) -> None:
        """Store a fresh code, creating the row if this is the first attempt.

        ``attempts`` resets to zero because the attempt ceiling belongs to the
        code, not to the number: a new code that inherited the old one's failed
        guesses would be unusable the moment somebody mistyped five times.

        ``send_count`` increments instead, because that ceiling belongs to the
        number — it is what stops this endpoint being used to text a stranger
        indefinitely.
        """
        async with self.async_session() as session:
            statement = (
                pg_insert(VerifiedNumberModel)
                .values(
                    organization_id=organization_id,
                    phone_number=phone_number,
                    label=label,
                    status="pending",
                    code_hash=code_hash,
                    code_salt=code_salt,
                    code_expires_at=code_expires_at,
                    attempts=0,
                    send_count=1,
                    last_sent_at=sent_at,
                    created_by=created_by,
                )
                .on_conflict_do_update(
                    constraint="_verified_number_org_number_uc",
                    set_={
                        "code_hash": code_hash,
                        "code_salt": code_salt,
                        "code_expires_at": code_expires_at,
                        "attempts": 0,
                        "send_count": VerifiedNumberModel.send_count + 1,
                        "last_sent_at": sent_at,
                    },
                )
            )
            await session.execute(statement)
            await session.commit()

    async def record_verification_attempt(
        self, organization_id: int, phone_number: str
    ) -> None:
        """Count one wrong guess.

        Incremented in SQL rather than read-modify-written in Python: two
        simultaneous guesses would otherwise each read the same count and write
        the same value, turning the attempt ceiling into a suggestion.
        """
        async with self.async_session() as session:
            await session.execute(
                update(VerifiedNumberModel)
                .where(
                    VerifiedNumberModel.organization_id == organization_id,
                    VerifiedNumberModel.phone_number == phone_number,
                )
                .values(attempts=VerifiedNumberModel.attempts + 1)
            )
            await session.commit()

    async def mark_number_verified(
        self, organization_id: int, phone_number: str, *, verified_at: datetime
    ) -> None:
        """Promote the row, and destroy the code.

        Clearing the hash matters: a verified number keeping a usable code
        around is a credential nobody needs any more, and the safest place for
        it is nowhere.
        """
        async with self.async_session() as session:
            await session.execute(
                update(VerifiedNumberModel)
                .where(
                    VerifiedNumberModel.organization_id == organization_id,
                    VerifiedNumberModel.phone_number == phone_number,
                )
                .values(
                    status="verified",
                    verified_at=verified_at,
                    code_hash=None,
                    code_salt=None,
                    code_expires_at=None,
                    attempts=0,
                )
            )
            await session.commit()

    async def list_verified_numbers(
        self, organization_id: int
    ) -> Sequence[VerifiedNumberModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(VerifiedNumberModel)
                .where(VerifiedNumberModel.organization_id == organization_id)
                .order_by(VerifiedNumberModel.created_at.desc())
            )
            return result.scalars().all()

    async def remove_verified_number(
        self, organization_id: int, phone_number: str
    ) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                delete(VerifiedNumberModel).where(
                    VerifiedNumberModel.organization_id == organization_id,
                    VerifiedNumberModel.phone_number == phone_number,
                )
            )
            await session.commit()
            return result.rowcount > 0
