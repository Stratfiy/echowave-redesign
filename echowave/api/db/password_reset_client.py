from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from api.db.base_client import BaseDBClient
from api.db.models import PasswordResetChallengeModel, UserModel


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class PasswordResetClient(BaseDBClient):
    async def issue_password_reset(
        self, user_id: int, *, token_hash: str, expires_at: datetime, now: datetime
    ) -> bool:
        async with self.async_session() as session:
            # Every issuance and consumption takes the same user lock first.
            user = await session.scalar(
                select(UserModel).where(UserModel.id == user_id).with_for_update()
            )
            if not user:
                return False
            challenge = await session.get(PasswordResetChallengeModel, user_id)
            if challenge:
                if now - _aware(challenge.last_sent_at) < timedelta(seconds=60):
                    return False
                same_window = now - _aware(challenge.window_started_at) < timedelta(
                    hours=1
                )
                if same_window and challenge.send_count >= 5:
                    return False
                challenge.send_count = challenge.send_count + 1 if same_window else 1
                if not same_window:
                    challenge.window_started_at = now
                challenge.token_hash = token_hash
                challenge.expires_at = expires_at
                challenge.last_sent_at = now
            else:
                session.add(
                    PasswordResetChallengeModel(
                        user_id=user_id,
                        token_hash=token_hash,
                        expires_at=expires_at,
                        window_started_at=now,
                        last_sent_at=now,
                        send_count=1,
                    )
                )
            await session.commit()
            return True

    async def has_password_reset(self, *, token_hash: str, now: datetime) -> bool:
        async with self.async_session() as session:
            return (
                await session.scalar(
                    select(PasswordResetChallengeModel.user_id).where(
                        PasswordResetChallengeModel.token_hash == token_hash,
                        PasswordResetChallengeModel.expires_at > now,
                    )
                )
                is not None
            )

    async def consume_password_reset(
        self, *, token_hash: str, password_hash: str, now: datetime
    ) -> bool:
        async with self.async_session() as session:
            user_id = await session.scalar(
                select(PasswordResetChallengeModel.user_id).where(
                    PasswordResetChallengeModel.token_hash == token_hash
                )
            )
            if user_id is None:
                return False
            user = await session.scalar(
                select(UserModel).where(UserModel.id == user_id).with_for_update()
            )
            challenge = await session.scalar(
                select(PasswordResetChallengeModel)
                .where(PasswordResetChallengeModel.user_id == user_id)
                .with_for_update()
            )
            # Re-read after the user lock: another request may have consumed or replaced it.
            if (
                not user
                or not challenge
                or challenge.token_hash != token_hash
                or now >= _aware(challenge.expires_at)
            ):
                return False
            user.password_hash = password_hash
            user.auth_version += 1
            # MFA secrets/codes and API keys are not changed by password recovery.
            await session.delete(challenge)
            await session.commit()
            return True
