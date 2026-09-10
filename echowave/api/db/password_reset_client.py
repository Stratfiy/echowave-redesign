from datetime import datetime
from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.db.base_client import BaseDBClient
from api.db.models import PasswordResetChallengeModel, UserModel


class PasswordResetClient(BaseDBClient):
    async def get_password_reset_challenge(
        self, user_id: int
    ) -> Optional[PasswordResetChallengeModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(PasswordResetChallengeModel).where(
                    PasswordResetChallengeModel.user_id == user_id
                )
            )
            return result.scalars().first()

    async def upsert_password_reset_challenge(
        self,
        user_id: int,
        *,
        email: str,
        code_hash: str,
        code_salt: str,
        expires_at: datetime,
        sent_at: datetime,
    ) -> None:
        """Replace the live code, keeping the send count — same reasoning as
        the email-verification row: attempts belong to the code, sends to the
        address."""
        async with self.async_session() as session:
            statement = (
                pg_insert(PasswordResetChallengeModel)
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
                    constraint="_password_reset_user_uc",
                    set_={
                        "email": email,
                        "code_hash": code_hash,
                        "code_salt": code_salt,
                        "expires_at": expires_at,
                        "attempts": 0,
                        "send_count": (PasswordResetChallengeModel.send_count + 1),
                        "last_sent_at": sent_at,
                    },
                )
            )
            await session.execute(statement)
            await session.commit()

    async def record_password_reset_attempt(self, user_id: int) -> None:
        async with self.async_session() as session:
            await session.execute(
                update(PasswordResetChallengeModel)
                .where(PasswordResetChallengeModel.user_id == user_id)
                .values(attempts=PasswordResetChallengeModel.attempts + 1)
            )
            await session.commit()

    async def set_password_and_destroy_challenge(
        self, user_id: int, *, password_hash: str
    ) -> None:
        """The new password and the end of the code, in one transaction.

        A code that outlived the password it set would be a second way to set
        another one.
        """
        async with self.async_session() as session:
            await session.execute(
                update(UserModel)
                .where(UserModel.id == user_id)
                .values(password_hash=password_hash)
            )
            await session.execute(
                delete(PasswordResetChallengeModel).where(
                    PasswordResetChallengeModel.user_id == user_id
                )
            )
            await session.commit()
