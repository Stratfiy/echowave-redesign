from datetime import datetime
from typing import List, Optional

from sqlalchemy import and_, select

from api.db.base_client import BaseDBClient
from api.db.models import OAuthConnectionModel


class OAuthConnectionClient(BaseDBClient):
    """Client for org-scoped OAuth-connected accounts (e.g. Google)."""

    async def create_oauth_connection(
        self,
        organization_id: int,
        provider: str,
        external_account_email: str,
        encrypted_access_token: str,
        encrypted_refresh_token: Optional[str],
        token_expires_at: Optional[datetime],
        scopes: List[str],
        created_by: Optional[int] = None,
    ) -> OAuthConnectionModel:
        async with self.async_session() as session:
            connection = OAuthConnectionModel(
                organization_id=organization_id,
                provider=provider,
                external_account_email=external_account_email,
                encrypted_access_token=encrypted_access_token,
                encrypted_refresh_token=encrypted_refresh_token,
                token_expires_at=token_expires_at,
                scopes=scopes,
                created_by=created_by,
                is_active=True,
            )
            session.add(connection)
            await session.commit()
            await session.refresh(connection)
            return connection

    async def get_oauth_connection(
        self, connection_id: int, organization_id: int
    ) -> Optional[OAuthConnectionModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(OAuthConnectionModel).where(
                    and_(
                        OAuthConnectionModel.id == connection_id,
                        OAuthConnectionModel.organization_id == organization_id,
                    )
                )
            )
            return result.scalars().first()

    async def list_oauth_connections(
        self,
        organization_id: int,
        provider: Optional[str] = None,
        active_only: bool = True,
    ) -> List[OAuthConnectionModel]:
        async with self.async_session() as session:
            conditions = [OAuthConnectionModel.organization_id == organization_id]
            if provider:
                conditions.append(OAuthConnectionModel.provider == provider)
            if active_only:
                conditions.append(OAuthConnectionModel.is_active == True)  # noqa: E712
            result = await session.execute(
                select(OAuthConnectionModel).where(and_(*conditions))
            )
            return list(result.scalars().all())

    async def update_oauth_connection_tokens(
        self,
        connection_id: int,
        encrypted_access_token: str,
        token_expires_at: Optional[datetime],
        encrypted_refresh_token: Optional[str] = None,
    ) -> None:
        async with self.async_session() as session:
            result = await session.execute(
                select(OAuthConnectionModel).where(
                    OAuthConnectionModel.id == connection_id
                )
            )
            connection = result.scalars().first()
            if not connection:
                return
            connection.encrypted_access_token = encrypted_access_token
            connection.token_expires_at = token_expires_at
            if encrypted_refresh_token:
                connection.encrypted_refresh_token = encrypted_refresh_token
            await session.commit()

    async def deactivate_oauth_connection(
        self, connection_id: int, organization_id: int
    ) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                select(OAuthConnectionModel).where(
                    and_(
                        OAuthConnectionModel.id == connection_id,
                        OAuthConnectionModel.organization_id == organization_id,
                    )
                )
            )
            connection = result.scalars().first()
            if not connection:
                return False
            connection.is_active = False
            await session.commit()
            return True
