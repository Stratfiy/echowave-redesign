"""A bot posts its own events to a URL somebody pasted in.

The inbound half already worked: a bot has a trigger URL and an address, so
n8n can start one. Nothing went the other way, which made this product the
end of a chain rather than a link in one.

Most of what is guarded here is the part that goes wrong quietly:

**A URL typed by a customer is an SSRF hole until something checks it.** The
existing call-flow webhook never checked -- a node aimed at 169.254.169.254
made our own servers fetch our own metadata service and reported the status
code back. The gate already existed and was applied to model endpoints and
pre-call fetches, and not here.

**A refusal must be permanent.** A URL that points inward will point inward
in thirty seconds too, so retrying it is a loop that only fills a log.

**A signature must cover the exact bytes sent**, or the receiver rejects
deliveries for reasons nobody can see.

**A failed POST must not undo the row.** The event happened; the webhook is
downstream of that, like the bell beside it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.db import db_client
from api.enums import AgentEventKind
from api.services.integrations import bot_event_webhook
from api.services.workflow import agent_timeline, bot_notices


class TestWhatGoesOut:
    def test_no_kinds_named_means_the_ones_the_bell_carries(self):
        """Empty is a default, not "none": a webhook nobody configured in
        detail should carry the events people actually subscribe to."""
        for kind in bot_notices.NOTIFIABLE:
            assert bot_event_webhook.wanted([], kind) is True

    def test_the_default_set_is_read_now_not_frozen(self):
        """A kind the product gains later reaches a webhook set up today --
        the opposite of the stale-allowlist failure agent_timeline warns
        about. Empty stores nothing, so there is nothing to go stale."""
        assert bot_event_webhook.wanted([], AgentEventKind.ACTIVITY.value) is False
        with patch.object(
            bot_notices, "NOTIFIABLE", frozenset({AgentEventKind.ACTIVITY.value})
        ):
            assert bot_event_webhook.wanted([], AgentEventKind.ACTIVITY.value) is True

    def test_naming_kinds_is_not_limited_to_that_set(self):
        """Somebody wiring a real pipeline knows what they want; curating is
        the screen's job, not the endpoint's."""
        assert (
            bot_event_webhook.wanted(
                [AgentEventKind.ACTIVITY.value], AgentEventKind.ACTIVITY.value
            )
            is True
        )

    def test_a_named_set_excludes_everything_else(self):
        assert (
            bot_event_webhook.wanted(
                [AgentEventKind.ACTIVITY.value], AgentEventKind.OUTCOME_FILED.value
            )
            is False
        )

    def test_blank_entries_do_not_count_as_a_choice(self):
        """A list of empty strings is a form that submitted nothing, and
        treating it as a named set would silence the webhook."""
        assert bot_event_webhook.wanted(
            ["", "  "], AgentEventKind.OUTCOME_FILED.value
        ) == (AgentEventKind.OUTCOME_FILED.value in bot_notices.NOTIFIABLE)


class TestTheSignature:
    def test_it_covers_the_timestamp_and_the_body(self):
        body = bot_event_webhook.body_of({"event": "outcome_filed"})
        first = bot_event_webhook.sign("s3cret", "1000", body)
        assert first.startswith("sha256=")
        # A different timestamp is a different signature, or yesterday's
        # delivery can be replayed for ever.
        assert first != bot_event_webhook.sign("s3cret", "1001", body)

    def test_a_different_secret_does_not_verify(self):
        body = bot_event_webhook.body_of({"event": "outcome_filed"})
        assert bot_event_webhook.sign("a", "1000", body) != bot_event_webhook.sign(
            "b", "1000", body
        )

    def test_the_body_is_byte_stable(self):
        """A re-send has to hash identically, so the signature is over one
        encoding of the document and not whatever a dict iterates as."""
        one = bot_event_webhook.body_of({"b": 1, "a": 2})
        two = bot_event_webhook.body_of({"a": 2, "b": 1})
        assert one == two

    def test_the_secret_is_not_guessable(self):
        assert len(bot_event_webhook.new_secret()) >= 40
        assert bot_event_webhook.new_secret() != bot_event_webhook.new_secret()


@pytest.mark.asyncio
class TestPostingAnEvent:
    @staticmethod
    def _hook(**over):
        fields = {
            "url": "https://hooks.example.com/decibyl",
            "secret": "s3cret",
            "kinds": [],
            "is_active": True,
        }
        fields.update(over)
        return SimpleNamespace(**fields)

    async def test_a_wanted_event_is_queued(self):
        with (
            patch.object(
                db_client,
                "get_bot_event_webhook",
                new=AsyncMock(return_value=self._hook()),
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
                summary="Booked Tuesday at 4",
                event_id=44,
                payload={"slot": "tue-16"},
            )
        assert enqueue.await_count == 1
        body = create.await_args.kwargs
        assert body["endpoint_url"] == "https://hooks.example.com/decibyl"
        # One delivery per event, so a caller that runs twice sends once.
        assert body["node_key"] == "event:44"
        assert body["payload"]["summary"] == "Booked Tuesday at 4"
        assert body["payload"]["data"] == {"slot": "tue-16"}
        assert body["payload"]["bot"] == {"id": 3, "name": "Front desk"}

    async def test_an_unwanted_kind_is_not_queued(self):
        with (
            patch.object(
                db_client,
                "get_bot_event_webhook",
                new=AsyncMock(return_value=self._hook()),
            ),
            patch.object(
                db_client, "create_bot_event_delivery", new=AsyncMock()
            ) as create,
        ):
            await bot_event_webhook.post_event(
                organization_id=7,
                workflow_id=3,
                kind=AgentEventKind.ACTIVITY.value,
                summary="Read 3 passages",
                event_id=45,
                payload={},
            )
        create.assert_not_awaited()

    async def test_a_switched_off_webhook_sends_nothing(self):
        with (
            patch.object(
                db_client,
                "get_bot_event_webhook",
                new=AsyncMock(return_value=self._hook(is_active=False)),
            ),
            patch.object(
                db_client, "create_bot_event_delivery", new=AsyncMock()
            ) as create,
        ):
            await bot_event_webhook.post_event(
                organization_id=7,
                workflow_id=3,
                kind=AgentEventKind.OUTCOME_FILED.value,
                summary="Booked",
                event_id=46,
                payload={},
            )
        create.assert_not_awaited()

    async def test_a_row_with_no_bot_is_not_a_bots_webhook(self):
        """Decibyl's own lines and channel rows have no bot, and the field
        this reads is per bot. Sending them would need its own field to be
        honest about where they came from."""
        with patch.object(db_client, "get_bot_event_webhook", new=AsyncMock()) as read:
            await bot_event_webhook.post_event(
                organization_id=7,
                workflow_id=None,
                kind=AgentEventKind.OUTCOME_FILED.value,
                summary="Booked",
                event_id=47,
                payload={},
            )
        read.assert_not_awaited()

    async def test_a_broken_lookup_never_reaches_the_caller(self):
        """The row is already written and the bell has already rung. A
        webhook must not be able to undo something that happened."""
        with patch.object(
            db_client,
            "get_bot_event_webhook",
            new=AsyncMock(side_effect=RuntimeError("no")),
        ):
            await bot_event_webhook.post_event(
                organization_id=7,
                workflow_id=3,
                kind=AgentEventKind.OUTCOME_FILED.value,
                summary="Booked",
                event_id=48,
                payload={},
            )


@pytest.mark.asyncio
class TestTheTimelineHandsItOver:
    async def test_recording_an_event_offers_it_to_the_webhook(self):
        with (
            patch(
                "api.services.workflow.agent_timeline.db_client.record_agent_event",
                new=AsyncMock(return_value=77),
            ),
            patch(
                "api.services.workflow.agent_timeline._ring_the_bell", new=AsyncMock()
            ),
            patch(
                "api.services.workflow.agent_timeline._folder_for",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "api.services.workflow.agent_timeline.bot_event_webhook.post_event",
                new=AsyncMock(),
            ) as post,
        ):
            await agent_timeline.record(
                organization_id=7,
                kind=AgentEventKind.OUTCOME_FILED.value,
                summary="Booked Tuesday at 4",
                workflow_id=3,
            )
        # The id of the row just written, so a retried delivery can be
        # deduped by the receiver against the event it is about.
        assert post.await_args.kwargs["event_id"] == 77
        assert post.await_args.kwargs["workflow_id"] == 3

    async def test_a_row_that_was_not_written_is_not_sent(self):
        with (
            patch(
                "api.services.workflow.agent_timeline.db_client.record_agent_event",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch(
                "api.services.workflow.agent_timeline._folder_for",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "api.services.workflow.agent_timeline.bot_event_webhook.post_event",
                new=AsyncMock(),
            ) as post,
        ):
            await agent_timeline.record(
                organization_id=7,
                kind=AgentEventKind.OUTCOME_FILED.value,
                summary="Booked",
                workflow_id=3,
            )
        post.assert_not_awaited()


@pytest.mark.asyncio
class TestWhereItMayPoint:
    """The gate the delivery engine never had.

    A webhook node aimed at the metadata service made our own servers fetch
    our own network, and the status code came back on the delivery row. The
    check existed for model endpoints and pre-call fetches; delivery was
    missed.
    """

    @staticmethod
    def _delivery(url: str):
        return SimpleNamespace(
            id=5,
            delivery_uuid="d-1",
            organization_id=7,
            workflow_run_id=None,
            workflow_id=3,
            webhook_name="Front desk events",
            endpoint_url=url,
            http_method="POST",
            payload={"event": "outcome_filed"},
            custom_headers=None,
            credential_uuid=None,
            attempt_count=0,
            max_attempts=5,
        )

    async def test_an_inward_url_is_dead_lettered_not_retried(self):
        from api.tasks import webhook_delivery

        with (
            patch(
                "api.tasks.webhook_delivery.db_client.claim_webhook_delivery",
                new=AsyncMock(return_value=self._delivery("http://169.254.169.254/")),
            ),
            patch(
                "api.tasks.webhook_delivery.validate_user_configured_service_url",
                side_effect=ValueError("URL must resolve to a public IP address"),
            ),
            patch(
                "api.tasks.webhook_delivery.db_client.mark_webhook_delivery_dead_letter",
                new=AsyncMock(),
            ) as parked,
            patch(
                "api.tasks.webhook_delivery._handle_transient_failure", new=AsyncMock()
            ) as retried,
            patch("httpx.AsyncClient") as client,
        ):
            await webhook_delivery.deliver_webhook(None, 5)

        parked.assert_awaited()
        # Not a transient failure: the answer will be the same in thirty
        # seconds, so retrying only fills a log.
        retried.assert_not_awaited()
        client.assert_not_called()

    async def test_the_url_is_checked_on_every_attempt(self):
        """A hostname that resolved publicly when it was saved can be
        re-pointed afterwards, so save-time validation alone is a check that
        expires."""
        from api.tasks import webhook_delivery

        with (
            patch(
                "api.tasks.webhook_delivery.db_client.claim_webhook_delivery",
                new=AsyncMock(return_value=self._delivery("https://ok.example.com/")),
            ),
            patch(
                "api.tasks.webhook_delivery.validate_user_configured_service_url"
            ) as gate,
            patch(
                "api.tasks.webhook_delivery._build_headers",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "api.tasks.webhook_delivery.db_client.mark_webhook_delivery_succeeded",
                new=AsyncMock(),
            ),
            patch("httpx.AsyncClient") as client,
        ):
            response = SimpleNamespace(
                status_code=200, raise_for_status=lambda: None, text="ok"
            )
            instance = client.return_value.__aenter__.return_value
            instance.request = AsyncMock(return_value=response)
            await webhook_delivery.deliver_webhook(None, 5)

        gate.assert_called_once()
        assert gate.call_args.args[0] == "https://ok.example.com/"


@pytest.mark.asyncio
class TestTheEndpoint:
    """What the field on the screen will be talking to."""

    @staticmethod
    async def _put(body: dict):
        from httpx import ASGITransport, AsyncClient

        from api.app import app
        from api.services.auth.depends import (
            get_user,
            get_user_with_selected_organization,
        )

        who = SimpleNamespace(id=42, selected_organization_id=7)
        # The role dependency is built per call, so overriding a freshly
        # built one never matches the object the route captured. Override
        # what it depends on and let the real gate run against a real
        # membership -- which also keeps the admin check in the test rather
        # than stubbing it out.
        app.dependency_overrides[get_user] = lambda: who
        app.dependency_overrides[get_user_with_selected_organization] = lambda: who
        try:
            with (
                patch.object(
                    db_client,
                    "get_membership",
                    new=AsyncMock(return_value=SimpleNamespace(role="admin")),
                ),
                patch.object(
                    db_client,
                    "get_workflow",
                    new=AsyncMock(
                        return_value=SimpleNamespace(id=3, name="Front desk")
                    ),
                ),
                patch.object(
                    db_client, "get_bot_event_webhook", new=AsyncMock(return_value=None)
                ),
                patch.object(
                    db_client,
                    "save_bot_event_webhook",
                    new=AsyncMock(
                        return_value=(
                            SimpleNamespace(
                                url=body.get("url", ""),
                                kinds=body.get("kinds", []),
                                is_active=True,
                                updated_at=None,
                            ),
                            True,
                        )
                    ),
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    return await client.put(
                        "/api/v1/workflows/3/event-webhook", json=body
                    )
        finally:
            app.dependency_overrides.clear()

    async def test_an_inward_url_is_refused_while_the_field_is_on_screen(self):
        with patch(
            "api.routes.bot_event_webhooks.validate_user_configured_service_url",
            side_effect=ValueError("URL must resolve to a public IP address"),
        ):
            response = await self._put({"url": "http://169.254.169.254/"})
        assert response.status_code == 422
        assert "public IP" in response.json()["detail"]

    async def test_an_event_this_system_never_writes_is_named_not_dropped(self):
        """A kind silently ignored is a webhook somebody believes is
        subscribed to something it is not."""
        with patch(
            "api.routes.bot_event_webhooks.validate_user_configured_service_url"
        ):
            response = await self._put(
                {"url": "https://hooks.example.com/x", "kinds": ["order_shipped"]}
            )
        assert response.status_code == 422
        assert "order_shipped" in response.json()["detail"]

    async def test_creating_one_hands_back_the_secret_and_says_what_it_sends(self):
        with patch(
            "api.routes.bot_event_webhooks.validate_user_configured_service_url"
        ):
            response = await self._put({"url": "https://hooks.example.com/x"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["secret"]
        # "Empty means the bell's set" is a rule nobody should have to infer
        # from a blank field.
        assert payload["sending"] == sorted(bot_notices.NOTIFIABLE)
