"""An organisation's event webhooks are capped per hour.

A receiver is somebody else's server. Nothing stopped a bot that files an
outcome per row of a sheet from POSTing to it as fast as it could think,
and the delivery engine's retries on a receiver that buckled under that
would only have made it worse.

Over the cap the event is still recorded and still rings the bell; only the
POST is withheld, with a warning that says which account and which bot. The
count is scoped to the organisation, to runless rows (the event webhooks,
not the call-flow ones) and to the last hour, and the index the migration
adds covers exactly that predicate -- the question is asked on every event
a webhook wants, and must not be a scan of every delivery ever made.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api import constants
from api.db import db_client
from api.db.models import WebhookDeliveryModel
from api.enums import AgentEventKind
from api.services.integrations import bot_event_webhook


def _hook():
    return SimpleNamespace(
        url="https://hooks.example.com/decibyl", secret="s", kinds=[], is_active=True
    )


@pytest.mark.asyncio
class TestTheCap:
    async def test_over_the_cap_nothing_is_queued_and_nothing_raises(self):
        with (
            patch.object(
                db_client, "get_bot_event_webhook", new=AsyncMock(return_value=_hook())
            ),
            patch.object(
                db_client,
                "count_bot_event_deliveries_since",
                new=AsyncMock(return_value=constants.EVENT_WEBHOOK_HOURLY_CAP),
            ),
            patch.object(
                db_client, "create_bot_event_delivery", new=AsyncMock()
            ) as create,
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as enqueue,
        ):
            await bot_event_webhook.post_event(
                organization_id=7,
                workflow_id=3,
                kind=AgentEventKind.OUTCOME_FILED.value,
                summary="Booked",
                event_id=1,
                payload={},
            )
        create.assert_not_awaited()
        enqueue.assert_not_awaited()

    async def test_under_the_cap_the_delivery_goes_ahead(self):
        with (
            patch.object(
                db_client, "get_bot_event_webhook", new=AsyncMock(return_value=_hook())
            ),
            patch.object(
                db_client,
                "count_bot_event_deliveries_since",
                new=AsyncMock(return_value=constants.EVENT_WEBHOOK_HOURLY_CAP - 1),
            ),
            patch.object(
                db_client,
                "get_workflow",
                new=AsyncMock(return_value=SimpleNamespace(name="Front desk")),
            ),
            patch.object(
                db_client,
                "create_bot_event_delivery",
                new=AsyncMock(return_value=(SimpleNamespace(id=9), True)),
            ) as create,
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as enqueue,
        ):
            await bot_event_webhook.post_event(
                organization_id=7,
                workflow_id=3,
                kind=AgentEventKind.OUTCOME_FILED.value,
                summary="Booked",
                event_id=2,
                payload={},
            )
        create.assert_awaited_once()
        enqueue.assert_awaited_once()

    async def test_the_count_is_asked_for_the_last_hour_of_this_organisation(self):
        with (
            patch.object(
                db_client, "get_bot_event_webhook", new=AsyncMock(return_value=_hook())
            ),
            patch.object(
                db_client,
                "count_bot_event_deliveries_since",
                new=AsyncMock(return_value=0),
            ) as count,
            patch.object(db_client, "get_workflow", new=AsyncMock(return_value=None)),
            patch.object(
                db_client,
                "create_bot_event_delivery",
                new=AsyncMock(return_value=(SimpleNamespace(id=9), True)),
            ),
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()),
        ):
            before = datetime.now(UTC)
            await bot_event_webhook.post_event(
                organization_id=7,
                workflow_id=3,
                kind=AgentEventKind.OUTCOME_FILED.value,
                summary="Booked",
                event_id=3,
                payload={},
            )
        kwargs = count.await_args.kwargs
        assert kwargs["organization_id"] == 7
        # About an hour ago, not "ever".
        age = (before - kwargs["since"]).total_seconds()
        assert 3590 <= age <= 3610

    async def test_an_unwanted_kind_is_not_counted_at_all(self):
        """The cheap gates come first. A kind this webhook never asked for
        costs no query."""
        with (
            patch.object(
                db_client, "get_bot_event_webhook", new=AsyncMock(return_value=_hook())
            ),
            patch.object(
                db_client, "count_bot_event_deliveries_since", new=AsyncMock()
            ) as count,
        ):
            await bot_event_webhook.post_event(
                organization_id=7,
                workflow_id=3,
                kind=AgentEventKind.ACTIVITY.value,
                summary="Read 3 passages",
                event_id=4,
                payload={},
            )
        count.assert_not_awaited()


@pytest.mark.asyncio
class TestTheCountItself:
    """Read off the compiled SQL: what matters is the predicate the database
    receives."""

    async def test_it_counts_this_organisations_runless_rows_since(self):
        captured: dict[str, str] = {}

        class _Recorder:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, query):
                captured["sql"] = str(
                    query.compile(compile_kwargs={"literal_binds": True})
                )
                result = MagicMock()
                result.scalar_one.return_value = 5
                return result

        with patch.object(db_client, "async_session", lambda: _Recorder()):
            n = await db_client.count_bot_event_deliveries_since(
                organization_id=7, since=datetime(2026, 9, 17, 20, 0, tzinfo=UTC)
            )
        assert n == 5
        sql = captured["sql"]
        assert "organization_id = 7" in sql
        assert "workflow_run_id IS NULL" in sql
        assert "created_at >=" in sql


class TestTheIndex:
    def test_the_question_is_indexed(self):
        names = {ix.name for ix in WebhookDeliveryModel.__table__.indexes}
        assert "ix_webhook_deliveries_org_recent_events" in names

    def test_the_cap_is_sane(self):
        """Above anything a person wires up on purpose, below anything that
        takes a receiver down."""
        assert 60 <= constants.EVENT_WEBHOOK_HOURLY_CAP <= 10_000
