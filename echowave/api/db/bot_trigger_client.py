"""Reads and writes for bot triggers -- the doorbell.

Kept as small as the routine client, for the same reason: whether an event
should start a run is decided in ``services/workflow/bot_triggers.py`` as
pure functions over the payload. This layer fetches rows and stamps them.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select

from api.db.base_client import BaseDBClient
from api.db.models import BotTriggerModel


class BotTriggerClient(BaseDBClient):
    async def bot_triggers_for_workflow(
        self, workflow_id: int, *, organization_id: int
    ) -> Sequence[BotTriggerModel]:
        """This bot's triggers. Org-scoped."""
        async with self.async_session() as session:
            result = await session.execute(
                select(BotTriggerModel)
                .where(
                    BotTriggerModel.workflow_id == workflow_id,
                    BotTriggerModel.organization_id == organization_id,
                )
                .order_by(BotTriggerModel.id)
            )
            return result.scalars().all()

    async def get_bot_trigger(
        self, trigger_id: int, *, organization_id: int, workflow_id: int
    ) -> BotTriggerModel | None:
        """One trigger, or None unless it is this organisation's AND this bot's.

        Both ids come from the URL, so checking only one would let a trigger
        be edited through another bot's path -- same account, wrong bot.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(BotTriggerModel).where(
                    BotTriggerModel.id == trigger_id,
                    BotTriggerModel.organization_id == organization_id,
                    BotTriggerModel.workflow_id == workflow_id,
                )
            )
            return result.scalar_one_or_none()

    async def get_bot_trigger_by_uuid(self, uuid: str) -> BotTriggerModel | None:
        """The public lookup. No organisation: a webhook sender has none.

        The row carries its own ``organization_id``, and the route derives
        the tenant from it rather than from anything in the request -- which
        is the one case AGENTS.md allows, and it is validated by the secret.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(BotTriggerModel).where(BotTriggerModel.uuid == uuid)
            )
            return result.scalar_one_or_none()

    async def create_bot_trigger(
        self, *, organization_id: int, workflow_id: int, **fields
    ) -> BotTriggerModel:
        async with self.async_session() as session:
            trigger = BotTriggerModel(
                organization_id=organization_id,
                workflow_id=workflow_id,
                **fields,
            )
            session.add(trigger)
            await session.commit()
            await session.refresh(trigger)
            return trigger

    async def update_bot_trigger(
        self, trigger_id: int, *, organization_id: int, workflow_id: int, **fields
    ) -> BotTriggerModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(BotTriggerModel).where(
                    BotTriggerModel.id == trigger_id,
                    BotTriggerModel.organization_id == organization_id,
                    BotTriggerModel.workflow_id == workflow_id,
                )
            )
            trigger = result.scalar_one_or_none()
            if trigger is None:
                return None
            for key, value in fields.items():
                setattr(trigger, key, value)
            await session.commit()
            await session.refresh(trigger)
            return trigger

    async def delete_bot_trigger(
        self, trigger_id: int, *, organization_id: int, workflow_id: int
    ) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                select(BotTriggerModel).where(
                    BotTriggerModel.id == trigger_id,
                    BotTriggerModel.organization_id == organization_id,
                    BotTriggerModel.workflow_id == workflow_id,
                )
            )
            trigger = result.scalar_one_or_none()
            if trigger is None:
                return False
            await session.delete(trigger)
            await session.commit()
            return True

    async def mark_bot_trigger_fired(self, trigger_id: int) -> None:
        """Stamp the firing. Best effort: a failed stamp must not stop a run."""
        async with self.async_session() as session:
            trigger = await session.get(BotTriggerModel, trigger_id)
            if trigger is None:
                return
            trigger.last_fired_at = datetime.now(UTC)
            trigger.fired_count = int(trigger.fired_count or 0) + 1
            await session.commit()
