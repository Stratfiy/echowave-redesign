"""Decibyl's private browser: sessions, saved logins, the staff site list.

A session and a saved login belong to one **person** in one organisation, so
every read a person can cause is filtered by ``organization_id`` *and*
``user_id`` in the query itself. The worker that runs a session reads it by
its uuid alone (``get_browser_session_for_worker``): the uuid came from the
job the platform queued, never from a request.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update

from api.db.base_client import BaseDBClient
from api.db.browser_models import (
    BrowserSessionModel,
    BrowserSiteLoginModel,
    BrowserSiteRuleModel,
)
from api.db.models import AdminActionLogModel

#: The states a session can still move out of.
LIVE_STATES = ("starting", "working", "waiting_for_you", "captcha", "taken_over")


class BrowserClient(BaseDBClient):
    # --- sessions -----------------------------------------------------------

    async def create_browser_session(
        self,
        *,
        session_uuid: str,
        organization_id: int,
        user_id: int,
        thread_id: str | None,
        task: str,
        request: str,
        sites: list[str],
        allowed_verbs: list[str],
        limits: dict[str, int],
    ) -> BrowserSessionModel:
        async with self.async_session() as session:
            row = BrowserSessionModel(
                session_uuid=session_uuid,
                organization_id=organization_id,
                user_id=user_id,
                thread_id=thread_id,
                task=task,
                request=request,
                sites=list(sites),
                allowed_verbs=list(allowed_verbs),
                state="starting",
                state_note="Opening a private browser…",
                limits=dict(limits),
                used={"steps": 0, "minutes": 0, "cost_paise": 0},
                steps=[],
                keep_login_sites=[],
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def get_browser_session(
        self, session_uuid: str, *, organization_id: int, user_id: int
    ) -> BrowserSessionModel | None:
        """The person's own session, or None -- for anyone else, too."""
        async with self.async_session() as session:
            result = await session.execute(
                select(BrowserSessionModel).where(
                    BrowserSessionModel.session_uuid == session_uuid,
                    BrowserSessionModel.organization_id == organization_id,
                    BrowserSessionModel.user_id == user_id,
                )
            )
            return result.scalar_one_or_none()

    async def get_browser_session_for_worker(
        self, session_uuid: str
    ) -> BrowserSessionModel | None:
        """By uuid alone. Only the worker and the approval job call this, with
        a uuid the platform wrote; never with one from a request."""
        async with self.async_session() as session:
            result = await session.execute(
                select(BrowserSessionModel).where(
                    BrowserSessionModel.session_uuid == session_uuid
                )
            )
            return result.scalar_one_or_none()

    async def claim_browser_session(self, session_id: int) -> bool:
        """Mark a starting session as owned by the job about to run it. One
        winner: a retried job finds it claimed and opens nothing."""
        async with self.async_session() as session:
            result = await session.execute(
                update(BrowserSessionModel)
                .where(
                    BrowserSessionModel.id == session_id,
                    BrowserSessionModel.state == "starting",
                    BrowserSessionModel.driver_handle.is_(None),
                )
                .values(driver_handle="claimed", updated_at=datetime.now(UTC))
            )
            await session.commit()
            return (result.rowcount or 0) == 1

    async def update_browser_session(
        self, session_id: int, **fields: Any
    ) -> BrowserSessionModel | None:
        fields["updated_at"] = datetime.now(UTC)
        async with self.async_session() as session:
            await session.execute(
                update(BrowserSessionModel)
                .where(BrowserSessionModel.id == session_id)
                .values(**fields)
            )
            await session.commit()
            result = await session.execute(
                select(BrowserSessionModel).where(BrowserSessionModel.id == session_id)
            )
            return result.scalar_one_or_none()

    async def stale_browser_sessions(
        self, *, older_than: datetime
    ) -> list[BrowserSessionModel]:
        """Sessions still marked live that were created before ``older_than``:
        past any limit a running job would have stopped them at."""
        async with self.async_session() as session:
            result = await session.execute(
                select(BrowserSessionModel).where(
                    BrowserSessionModel.state.in_(LIVE_STATES),
                    BrowserSessionModel.created_at < older_than,
                )
            )
            return list(result.scalars().all())

    async def count_live_browser_sessions(self) -> int:
        async with self.async_session() as session:
            result = await session.execute(
                select(func.count(BrowserSessionModel.id)).where(
                    BrowserSessionModel.state.in_(LIVE_STATES)
                )
            )
            return int(result.scalar() or 0)

    # --- saved logins (cookies only, encrypted) --------------------------------

    async def save_browser_login(
        self,
        *,
        organization_id: int,
        user_id: int,
        site: str,
        cookies_encrypted: bytes,
        cookie_count: int,
    ) -> BrowserSiteLoginModel:
        async with self.async_session() as session:
            result = await session.execute(
                select(BrowserSiteLoginModel).where(
                    BrowserSiteLoginModel.organization_id == organization_id,
                    BrowserSiteLoginModel.user_id == user_id,
                    BrowserSiteLoginModel.site == site,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = BrowserSiteLoginModel(
                    organization_id=organization_id, user_id=user_id, site=site
                )
                session.add(row)
            row.cookies_encrypted = cookies_encrypted
            row.cookie_count = cookie_count
            row.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(row)
            return row

    async def list_browser_logins(
        self, *, organization_id: int, user_id: int
    ) -> list[BrowserSiteLoginModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(BrowserSiteLoginModel)
                .where(
                    BrowserSiteLoginModel.organization_id == organization_id,
                    BrowserSiteLoginModel.user_id == user_id,
                )
                .order_by(BrowserSiteLoginModel.site)
            )
            return list(result.scalars().all())

    async def touch_browser_login(self, login_id: int) -> None:
        async with self.async_session() as session:
            await session.execute(
                update(BrowserSiteLoginModel)
                .where(BrowserSiteLoginModel.id == login_id)
                .values(last_used_at=datetime.now(UTC))
            )
            await session.commit()

    async def delete_browser_login(
        self, login_id: int, *, organization_id: int, user_id: int
    ) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                delete(BrowserSiteLoginModel).where(
                    BrowserSiteLoginModel.id == login_id,
                    BrowserSiteLoginModel.organization_id == organization_id,
                    BrowserSiteLoginModel.user_id == user_id,
                )
            )
            await session.commit()
            return (result.rowcount or 0) > 0

    # --- the staff site list ----------------------------------------------------

    async def list_browser_site_rules(self) -> list[BrowserSiteRuleModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(BrowserSiteRuleModel).order_by(BrowserSiteRuleModel.site)
            )
            return list(result.scalars().all())

    async def set_browser_site_rule(
        self, *, site: str, rule: str, reason: str, set_by_user_id: int | None
    ) -> BrowserSiteRuleModel:
        async with self.async_session() as session:
            result = await session.execute(
                select(BrowserSiteRuleModel).where(BrowserSiteRuleModel.site == site)
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = BrowserSiteRuleModel(site=site)
                session.add(row)
            before = row.rule
            row.rule = rule
            row.reason = reason
            row.set_by_user_id = set_by_user_id
            row.updated_at = datetime.now(UTC)
            if set_by_user_id is not None:
                session.add(
                    AdminActionLogModel(
                        actor_user_id=set_by_user_id,
                        action="browser_site_rule_set",
                        note=f"site={site}; {before or 'none'} -> {rule}; {reason}"[
                            :500
                        ],
                    )
                )
            await session.commit()
            await session.refresh(row)
            return row

    async def delete_browser_site_rule(
        self, site: str, *, actor_user_id: int | None = None
    ) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                delete(BrowserSiteRuleModel).where(BrowserSiteRuleModel.site == site)
            )
            if actor_user_id is not None and (result.rowcount or 0) > 0:
                session.add(
                    AdminActionLogModel(
                        actor_user_id=actor_user_id,
                        action="browser_site_rule_removed",
                        note=f"site={site}"[:500],
                    )
                )
            await session.commit()
            return (result.rowcount or 0) > 0
