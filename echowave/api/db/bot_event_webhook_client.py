"""Where a bot posts its events, and the deliveries that carry them.

The configuration is one row per bot. The deliveries go through
``webhook_deliveries`` -- the same table, task, backoff, dead-letter and
sweeper the call-flow webhook uses, because a second delivery engine is a
second set of bugs in the part nobody watches until a customer says they
never got the callback.
"""

from datetime import UTC, datetime
from typing import Optional, Sequence, Tuple

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from api.db.base_client import BaseDBClient
from api.db.models import BotEventWebhookModel, WebhookDeliveryModel, WorkflowModel


class BotEventWebhookClient(BaseDBClient):
    async def get_bot_event_webhook(
        self, workflow_id: int, *, organization_id: int
    ) -> Optional[BotEventWebhookModel]:
        """This bot's webhook, or None.

        Org-scoped in the query rather than filtered after, the way
        ``get_routine`` is: the caller's organisation decides the row, so
        another tenant's id cannot read one back.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(BotEventWebhookModel).where(
                    BotEventWebhookModel.workflow_id == workflow_id,
                    BotEventWebhookModel.organization_id == organization_id,
                )
            )
            return result.scalar_one_or_none()

    async def save_bot_event_webhook(
        self,
        *,
        workflow_id: int,
        organization_id: int,
        url: str,
        secret: str,
        kinds: Sequence[str],
        is_active: bool = True,
    ) -> Tuple[BotEventWebhookModel, bool]:
        """Create it, or update the one already there. Returns (row, created).

        ``created`` is what tells the caller whether to show the secret: it
        is generated once and never sent again, so a save that only moved the
        URL must not look like a rotation.
        """
        async with self.async_session() as session:
            existing = await session.execute(
                select(BotEventWebhookModel).where(
                    BotEventWebhookModel.workflow_id == workflow_id,
                    BotEventWebhookModel.organization_id == organization_id,
                )
            )
            row = existing.scalar_one_or_none()
            if row is not None:
                await session.execute(
                    update(BotEventWebhookModel)
                    .where(BotEventWebhookModel.id == row.id)
                    .values(
                        url=url,
                        kinds=list(kinds),
                        is_active=is_active,
                        updated_at=datetime.now(UTC),
                    )
                )
                await session.commit()
                await session.refresh(row)
                return row, False

            row = BotEventWebhookModel(
                workflow_id=workflow_id,
                organization_id=organization_id,
                url=url,
                secret=secret,
                kinds=list(kinds),
                is_active=is_active,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # Two saves at once. The other one won; report it as the
                # update it effectively is, rather than failing a request
                # whose intent is already satisfied.
                await session.rollback()
                again = await session.execute(
                    select(BotEventWebhookModel).where(
                        BotEventWebhookModel.workflow_id == workflow_id
                    )
                )
                other = again.scalar_one_or_none()
                if other is not None:
                    return other, False
                raise
            await session.refresh(row)
            return row, True

    async def rotate_bot_event_webhook_secret(
        self, *, workflow_id: int, organization_id: int, secret: str
    ) -> bool:
        """Replace the signing secret.

        Its own method rather than a field on the save, because rotating
        breaks every receiver that has not been given the new one. That is
        sometimes exactly what somebody wants -- a leaked secret -- and never
        something a save about the URL should do on the way past.
        """
        async with self.async_session() as session:
            result = await session.execute(
                update(BotEventWebhookModel)
                .where(
                    BotEventWebhookModel.workflow_id == workflow_id,
                    BotEventWebhookModel.organization_id == organization_id,
                )
                .values(secret=secret, updated_at=datetime.now(UTC))
            )
            await session.commit()
            return bool(result.rowcount)

    async def delete_bot_event_webhook(
        self, workflow_id: int, *, organization_id: int
    ) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                delete(BotEventWebhookModel).where(
                    BotEventWebhookModel.workflow_id == workflow_id,
                    BotEventWebhookModel.organization_id == organization_id,
                )
            )
            await session.commit()
            return bool(result.rowcount)

    async def count_bot_event_deliveries_since(
        self, *, organization_id: int, since: datetime
    ) -> int:
        """How many event deliveries this organisation has queued since.

        Runless rows only -- the event webhooks -- so a busy call day does
        not count against a quiet webhook, and the partial index the
        migration adds covers exactly this predicate.
        """
        from sqlalchemy import func

        async with self.async_session() as session:
            result = await session.execute(
                select(func.count())
                .select_from(WebhookDeliveryModel)
                .where(
                    WebhookDeliveryModel.organization_id == organization_id,
                    WebhookDeliveryModel.workflow_run_id.is_(None),
                    WebhookDeliveryModel.created_at >= since,
                )
            )
            return int(result.scalar_one() or 0)

    async def create_bot_event_delivery(
        self,
        *,
        workflow_id: int,
        organization_id: int,
        endpoint_url: str,
        payload: dict,
        max_attempts: int,
        node_key: str,
        webhook_name: Optional[str] = None,
        custom_headers: Optional[list] = None,
    ) -> Tuple[Optional[WebhookDeliveryModel], bool]:
        """Get-or-create the pending delivery for this bot and this event.

        Idempotent on ``(workflow_id, webhook_node_id)`` among rows with no
        run, which is the partial index the migration adds. One event is one
        delivery however many times the caller is retried.

        The bot is checked against the organisation here rather than trusted,
        the same check ``create_webhook_delivery`` makes of a run: a delivery
        is an outbound request with a customer's secret on it, and the tenancy
        question is not one to answer from an argument.
        """
        async with self.async_session() as session:
            owner = await session.execute(
                select(WorkflowModel.organization_id).where(
                    WorkflowModel.id == workflow_id
                )
            )
            found = owner.scalar_one_or_none()
            if found is None or found != organization_id:
                raise ValueError(
                    f"Workflow {workflow_id} is not in organization {organization_id}"
                )

            delivery = WebhookDeliveryModel(
                workflow_run_id=None,
                workflow_id=workflow_id,
                organization_id=organization_id,
                webhook_name=webhook_name,
                webhook_node_id=node_key,
                endpoint_url=endpoint_url,
                http_method="POST",
                payload=payload,
                custom_headers=custom_headers,
                credential_uuid=None,
                max_attempts=max_attempts,
                status="pending",
                attempt_count=0,
                scheduled_for=datetime.now(UTC),
            )
            session.add(delivery)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.execute(
                    select(WebhookDeliveryModel).where(
                        WebhookDeliveryModel.workflow_id == workflow_id,
                        WebhookDeliveryModel.webhook_node_id == node_key,
                        WebhookDeliveryModel.workflow_run_id.is_(None),
                    )
                )
                row = existing.scalar_one_or_none()
                if row is not None:
                    return row, False
                raise
            await session.refresh(delivery)
            return delivery, True
