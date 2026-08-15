from datetime import datetime
from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.db.base_client import BaseDBClient
from api.db.models import EmailVerificationChallengeModel, UserModel


class EmailVerificationClient(BaseDBClient):
    async def get_email_challenge(
        self, user_id: int
    ) -> Optional[EmailVerificationChallengeModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(EmailVerificationChallengeModel).where(
                    EmailVerificationChallengeModel.user_id == user_id
                )
            )
            return result.scalars().first()

    async def upsert_email_challenge(
        self,
        user_id: int,
        *,
        email: str,
        code_hash: str,
        code_salt: str,
        expires_at: datetime,
        sent_at: datetime,
    ) -> None:
        """Replace the live code, keeping the send count.

        attempts resets because the ceiling belongs to the code — a fresh code
        that inherited five failed guesses would be unusable on arrival.
        send_count increments because that ceiling belongs to the address, and
        is what stops this being a way to mail a stranger indefinitely.
        """
        async with self.async_session() as session:
            statement = (
                pg_insert(EmailVerificationChallengeModel)
                .values(
                    user_id=user_id,
                    email=email,
                    code_hash=code_hash,
                    code_salt=code_salt,
                    expires_at=expires_at,
                    attempts=0,
                    send_count=1,
                    last_sent_at=sent_at,
                )
                .on_conflict_do_update(
                    constraint="_email_verification_user_uc",
                    set_={
                        "email": email,
                        "code_hash": code_hash,
                        "code_salt": code_salt,
                        "expires_at": expires_at,
                        "attempts": 0,
                        "send_count": (EmailVerificationChallengeModel.send_count + 1),
                        "last_sent_at": sent_at,
                    },
                )
            )
            await session.execute(statement)
            await session.commit()

    async def record_email_attempt(self, user_id: int) -> None:
        """Count one wrong guess, in SQL.

        Read-modify-write in Python would let two simultaneous guesses each
        read the same count and write the same value, turning the attempt
        ceiling into a suggestion.
        """
        async with self.async_session() as session:
            await session.execute(
                update(EmailVerificationChallengeModel)
                .where(EmailVerificationChallengeModel.user_id == user_id)
                .values(attempts=EmailVerificationChallengeModel.attempts + 1)
            )
            await session.commit()

    async def mark_email_verified(self, user_id: int, *, verified_at: datetime) -> None:
        """Record the fact and destroy the challenge.

        The row goes rather than being marked spent: a used code sitting in the
        database is a credential nobody needs, and the safest place for it is
        nowhere.
        """
        async with self.async_session() as session:
            await session.execute(
                update(UserModel)
                .where(UserModel.id == user_id)
                .values(email_verified_at=verified_at)
            )
            await session.execute(
                delete(EmailVerificationChallengeModel).where(
                    EmailVerificationChallengeModel.user_id == user_id
                )
            )
            await session.commit()
