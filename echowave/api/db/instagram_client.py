"""Storage for the Instagram accounts workspaces connected.

Every read that a person's request makes is scoped by ``organization_id``.
The one that is not -- ``get_instagram_account_by_ig_user`` -- is the
webhook's: Instagram names the account a message was sent to, and that id
is what says which workspace it belongs to.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.db.base_client import BaseDBClient
from api.db.models import InstagramAccountModel


class InstagramClient(BaseDBClient):
    async def save_instagram_account(
        self,
        *,
        organization_id: int,
        ig_user_id: str,
        username: str,
        encrypted_token: str,
        token_expires_at: datetime | None,
        connected_by: int | None,
    ) -> InstagramAccountModel:
        """Connect (or reconnect) an account to this workspace. An account
        connected elsewhere before moves here, and its agent is cleared:
        the agent belonged to the other workspace."""
        async with self.async_session() as session:
            statement = (
                pg_insert(InstagramAccountModel)
                .values(
                    organization_id=organization_id,
                    ig_user_id=ig_user_id,
                    username=username,
                    encrypted_token=encrypted_token,
                    token_expires_at=token_expires_at,
                    connected_by=connected_by,
                    is_active=True,
                )
                .on_conflict_do_update(
                    index_elements=[InstagramAccountModel.ig_user_id],
                    set_={
                        "username": username,
                        "encrypted_token": encrypted_token,
                        "token_expires_at": token_expires_at,
                        "connected_by": connected_by,
                        "is_active": True,
                        # Kept on a reconnect from the same workspace; an
                        # agent of another workspace never answers here.
                        "workflow_id": case(
                            (
                                InstagramAccountModel.organization_id
                                == organization_id,
                                InstagramAccountModel.workflow_id,
                            ),
                            else_=None,
                        ),
                        "organization_id": organization_id,
                    },
                )
                .returning(InstagramAccountModel)
            )
            row = (await session.execute(statement)).scalar_one()
            await session.commit()
            return row

    async def get_instagram_account(
        self, organization_id: int
    ) -> InstagramAccountModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(InstagramAccountModel)
                .where(
                    InstagramAccountModel.organization_id == organization_id,
                    InstagramAccountModel.is_active.is_(True),
                )
                .order_by(InstagramAccountModel.updated_at.desc())
                .limit(1)
            )
            return result.scalars().first()

    async def get_instagram_account_by_ig_user(
        self, ig_user_id: str
    ) -> InstagramAccountModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(InstagramAccountModel).where(
                    InstagramAccountModel.ig_user_id == ig_user_id,
                    InstagramAccountModel.is_active.is_(True),
                )
            )
            return result.scalars().first()

    async def set_instagram_agent(
        self, *, organization_id: int, account_id: int, workflow_id: int | None
    ) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                update(InstagramAccountModel)
                .where(
                    InstagramAccountModel.id == account_id,
                    InstagramAccountModel.organization_id == organization_id,
                )
                .values(workflow_id=workflow_id)
            )
            await session.commit()
            return bool(result.rowcount)

    async def update_instagram_token(
        self,
        *,
        account_id: int,
        encrypted_token: str,
        token_expires_at: datetime | None,
    ) -> None:
        async with self.async_session() as session:
            await session.execute(
                update(InstagramAccountModel)
                .where(InstagramAccountModel.id == account_id)
                .values(
                    encrypted_token=encrypted_token,
                    token_expires_at=token_expires_at,
                )
            )
            await session.commit()

    async def disconnect_instagram_account(
        self, *, organization_id: int, account_id: int
    ) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                update(InstagramAccountModel)
                .where(
                    InstagramAccountModel.id == account_id,
                    InstagramAccountModel.organization_id == organization_id,
                )
                .values(is_active=False, workflow_id=None)
            )
            await session.commit()
            return bool(result.rowcount)
