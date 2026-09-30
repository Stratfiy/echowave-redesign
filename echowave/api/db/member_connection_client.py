"""Which member authorised which Composio connected account (WS-1).

Composio holds the credentials; this table holds only the mapping from a
connected-account id to the member whose tenant it lives under, so a tool
configured with another member's account is refused here, before a network
call, rather than by Composio afterwards.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import delete, select

from api.db.base_client import BaseDBClient
from api.db.models import MemberConnectionModel


class MemberConnectionClient(BaseDBClient):
    async def record_member_connection(
        self, *, organization_id: int, user_id: int, toolkit: str
    ) -> MemberConnectionModel:
        """A member pressed Connect for an app. The account id is learned later."""
        toolkit = toolkit.strip().lower()
        async with self.async_session() as session:
            existing = (
                await session.execute(
                    select(MemberConnectionModel).where(
                        MemberConnectionModel.organization_id == organization_id,
                        MemberConnectionModel.user_id == user_id,
                        MemberConnectionModel.toolkit == toolkit,
                        MemberConnectionModel.connected_account_id.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing
            row = MemberConnectionModel(
                organization_id=organization_id, user_id=user_id, toolkit=toolkit
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def learn_member_connections(
        self,
        *,
        organization_id: int,
        user_id: int,
        accounts: list[tuple[str, str]],
    ) -> None:
        """Attach the account ids Composio lists under this member's tenant.

        ``accounts`` is ``(toolkit, connected_account_id)`` pairs. A pair
        already known is left alone; a placeholder row from Connect (no id
        yet) is replaced by the row that has one.
        """
        async with self.async_session() as session:
            known = set(
                (
                    await session.execute(
                        select(MemberConnectionModel.connected_account_id).where(
                            MemberConnectionModel.organization_id == organization_id,
                            MemberConnectionModel.user_id == user_id,
                            MemberConnectionModel.connected_account_id.is_not(None),
                        )
                    )
                ).scalars()
            )
            learned_toolkits: set[str] = set()
            for toolkit, account_id in accounts:
                toolkit = (toolkit or "").strip().lower()
                if account_id in known:
                    continue
                session.add(
                    MemberConnectionModel(
                        organization_id=organization_id,
                        user_id=user_id,
                        toolkit=toolkit,
                        connected_account_id=account_id,
                    )
                )
                known.add(account_id)
                learned_toolkits.add(toolkit)
            if learned_toolkits:
                await session.execute(
                    delete(MemberConnectionModel).where(
                        MemberConnectionModel.organization_id == organization_id,
                        MemberConnectionModel.user_id == user_id,
                        MemberConnectionModel.toolkit.in_(learned_toolkits),
                        MemberConnectionModel.connected_account_id.is_(None),
                    )
                )
            await session.commit()

    async def member_connection_owner(
        self, *, organization_id: int, connected_account_id: str
    ) -> Optional[int]:
        """The member who owns this connected account, or None if unknown."""
        async with self.async_session() as session:
            return (
                await session.execute(
                    select(MemberConnectionModel.user_id).where(
                        MemberConnectionModel.organization_id == organization_id,
                        MemberConnectionModel.connected_account_id
                        == connected_account_id,
                    )
                )
            ).scalar_one_or_none()

    async def member_connections(
        self, *, organization_id: int, user_id: int
    ) -> list[MemberConnectionModel]:
        """Every connection this member has, placeholders included."""
        async with self.async_session() as session:
            return list(
                (
                    await session.execute(
                        select(MemberConnectionModel)
                        .where(
                            MemberConnectionModel.organization_id == organization_id,
                            MemberConnectionModel.user_id == user_id,
                        )
                        .order_by(MemberConnectionModel.id)
                    )
                ).scalars()
            )
