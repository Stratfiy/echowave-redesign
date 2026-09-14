"""Triggers (KAN-137): a sentence becomes a doorbell, the doorbell runs the bot.

The tests that matter are the refusals that do NOT happen: an unsaid field
becomes a question, not an error; a missing field at run time becomes a
line to the bot, not a 400 to the sender; a filtered event costs nothing.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.services.agent_builder.client import ModelReply, ToolCall
from api.services.agent_builder.settings import BuilderUnavailable
from api.services.billing import events as billing_events
from api.services.workflow import bot_triggers


class TestTheFilter:
    def test_no_rules_means_every_event(self):
        assert bot_triggers.matches([], {"anything": 1}) is True

    def test_numbers_compare_as_numbers(self):
        rules = [{"field": "order.total", "op": "gt", "value": "5000"}]
        assert bot_triggers.matches(rules, {"order": {"total": "5,250.00"}})
        assert not bot_triggers.matches(rules, {"order": {"total": 4999}})

    def test_strings_compare_without_case(self):
        rules = [{"field": "status", "op": "eq", "value": "Paid"}]
        assert bot_triggers.matches(rules, {"status": "paid"})
        assert not bot_triggers.matches(rules, {"status": "refunded"})

    def test_contains_reads_lists_and_text(self):
        rules = [{"field": "tags", "op": "contains", "value": "vip"}]
        assert bot_triggers.matches(rules, {"tags": ["VIP", "new"]})
        assert bot_triggers.matches(rules, {"tags": "vip,new"})
        assert not bot_triggers.matches(rules, {"tags": ["new"]})

    def test_exists_tells_null_from_absent(self):
        assert bot_triggers.matches(
            [{"field": "phone", "op": "exists"}], {"phone": "9"}
        )
        assert not bot_triggers.matches(
            [{"field": "phone", "op": "exists"}], {"phone": None}
        )
        assert bot_triggers.matches([{"field": "phone", "op": "missing"}], {})

    def test_all_rules_must_hold(self):
        rules = [
            {"field": "total", "op": "gte", "value": "100"},
            {"field": "status", "op": "ne", "value": "test"},
        ]
        assert bot_triggers.matches(rules, {"total": 100, "status": "live"})
        assert not bot_triggers.matches(rules, {"total": 100, "status": "test"})

    def test_an_unknown_op_never_matches(self):
        # A rule nobody can read must not widen the filter to every event.
        assert not bot_triggers.matches(
            [{"field": "x", "op": "regex", "value": "."}], {"x": 1}
        )

    def test_describe_reads_as_a_sentence(self):
        rules = [
            {"field": "total", "op": "gt", "value": "5000"},
            {"field": "phone", "op": "exists"},
        ]
        assert bot_triggers.describe(rules) == "total > 5000 and phone is present"
        assert bot_triggers.describe([]) == "every event"


class TestMissingFields:
    def test_only_required_fields_count(self):
        fields = [
            {"name": "customer.phone", "required": True},
            {"name": "note", "required": False},
        ]
        assert bot_triggers.missing_fields(fields, {"customer": {}}) == [
            "customer.phone"
        ]
        assert bot_triggers.missing_fields(fields, {"customer": {"phone": "9"}}) == []

    def test_the_bot_is_told_and_asked_to_ask(self):
        message, missing = bot_triggers.run_message(
            name="Big order",
            instruction="Message the customer.",
            fields=[{"name": "phone", "required": True}],
            payload={"total": 6000},
        )
        assert missing == ["phone"]
        assert "did not include: phone" in message
        assert "ask_for_decision" in message
        assert '"total": 6000' in message

    def test_a_huge_event_is_truncated_not_refused(self):
        message, _ = bot_triggers.run_message(
            name="t", instruction="", fields=[], payload={"blob": "x" * 20_000}
        )
        assert "truncated" in message
        assert len(message) < 8_000


class TestTheCompile:
    def test_model_arguments_become_a_plan(self):
        compiled = bot_triggers.from_arguments(
            {
                "name": "Big Shopify order",
                "fields": [
                    {"name": "total_price", "required": True},
                    {"name": "total_price"},
                    {"name": "customer.phone", "description": "to message"},
                ],
                "filter": [{"field": "total_price", "op": "GT", "value": 5000}],
                "instruction": "Check stock and message the customer.",
                "questions": [],
            },
            "when a big shopify order comes in",
        )
        assert compiled.ready
        assert compiled.name == "Big Shopify order"
        assert [f["name"] for f in compiled.fields] == ["total_price", "customer.phone"]
        assert compiled.filter == [
            {"field": "total_price", "op": "gt", "value": "5000"}
        ]

    def test_an_unsaid_thing_is_a_question_not_an_error(self):
        compiled = bot_triggers.from_arguments(
            {
                "name": "Big order",
                "instruction": "Message the customer.",
                "questions": [
                    {"field": "threshold", "question": "What counts as a big order?"}
                ],
            },
            "when a big order comes in message the customer",
        )
        assert not compiled.ready
        assert compiled.questions[0]["field"] == "threshold"

    def test_a_rule_nobody_can_read_becomes_a_question(self):
        compiled = bot_triggers.from_arguments(
            {
                "name": "x",
                "instruction": "y",
                "filter": [{"field": "total", "op": "between", "value": "1-5"}],
            },
            "sentence",
        )
        assert compiled.filter == []
        assert not compiled.ready
        assert "total" in compiled.questions[0]["question"]

    @pytest.mark.asyncio
    async def test_no_model_is_not_an_error(self):
        with patch.object(
            bot_triggers,
            "resolve_model",
            AsyncMock(side_effect=BuilderUnavailable("off")),
        ):
            compiled = await bot_triggers.compile(
                "When a form is filled, reply to the person", session=None
            )
        assert compiled.ready
        assert compiled.instruction == "When a form is filled, reply to the person"
        assert "every event" in compiled.note

    @pytest.mark.asyncio
    async def test_the_model_is_asked_once_with_the_answers(self):
        reply = ModelReply(
            text="",
            tool_calls=(
                ToolCall(
                    id="1",
                    name=bot_triggers.COMPILE_TOOL_NAME,
                    arguments={"name": "Big order", "instruction": "Do it."},
                ),
            ),
        )
        model = SimpleNamespace(provider="anthropic", model="m", api_key="k")
        with (
            patch.object(bot_triggers, "resolve_model", AsyncMock(return_value=model)),
            patch.object(
                bot_triggers.builder_client, "complete", AsyncMock(return_value=reply)
            ) as complete,
        ):
            compiled = await bot_triggers.compile(
                "when a big order comes in", answers={"threshold": "5000"}, session=None
            )
        assert compiled.ready and compiled.name == "Big order"
        prompt = complete.await_args.kwargs["conversation"].messages[0]["content"]
        assert "threshold: 5000" in prompt
        assert complete.await_args.kwargs["tools"][0]["name"] == "describe_trigger"


class TestThePrice:
    def test_one_credit_per_run(self):
        # A published price: a change needs a note in the study and on KAN-47.
        assert billing_events.credits_for(billing_events.TRIGGER_RUN) == 1
        assert billing_events.EVENT_LABELS[billing_events.TRIGGER_RUN] == "Trigger run"


def _trigger(**overrides):
    base = {
        "id": 5,
        "organization_id": 7,
        "workflow_id": 42,
        "uuid": "0a0a0a0a-0000-4000-8000-000000000001",
        "secret": "s3cret",
        "name": "Big order",
        "source": "webhook",
        "sentence": "when a big order comes in",
        "instruction": "Message the customer.",
        "fields": [{"name": "phone", "required": True}],
        "filter": [{"field": "total", "op": "gt", "value": "5000"}],
        "is_active": True,
        "created_by": 1,
        "created_at": None,
        "last_fired_at": None,
        "fired_count": 0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
class TestTheDoorbell:
    """The public route, with the store and the queue stood in for."""

    async def _post(
        self, trigger, *, headers=None, params=None, json=None, content=None
    ):
        from api.app import app
        from api.routes import public_triggers as route

        with (
            patch.object(
                route.db_client,
                "get_bot_trigger_by_uuid",
                AsyncMock(return_value=trigger),
            ),
            patch.object(
                route, "already_delivered", AsyncMock(return_value=None)
            ) as seen,
            patch.object(route, "count_trigger", AsyncMock()),
            patch.object(route, "remember_delivery", AsyncMock()) as remember,
            patch.object(route, "enqueue_job", AsyncMock()) as enqueue,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/public/triggers/{trigger.uuid}",
                    headers=headers or {},
                    params=params,
                    json=json,
                    content=content,
                )
        return response, enqueue, remember, seen

    async def test_the_wrong_secret_is_refused(self):
        response, enqueue, *_ = await self._post(
            _trigger(), headers={"X-Trigger-Secret": "nope"}, json={"total": 9000}
        )
        assert response.status_code == 401
        enqueue.assert_not_awaited()

    async def test_a_matching_event_is_queued_and_remembered(self):
        response, enqueue, remember, _ = await self._post(
            _trigger(),
            headers={"X-Trigger-Secret": "s3cret", "X-Event-Id": "evt_1"},
            json={"total": 9000, "phone": "9"},
        )
        assert response.status_code == 202
        assert response.json()["status"] == "accepted"
        args = enqueue.await_args.args
        assert args[0] == "run_bot_trigger" and args[1] == 5
        assert args[2]["total"] == 9000 and args[3] == "evt_1"
        assert remember.await_args.args[1] == "evt_1"

    async def test_the_secret_may_come_as_a_query_key(self):
        response, enqueue, *_ = await self._post(
            _trigger(), params={"key": "s3cret"}, json={"total": 9000}
        )
        assert response.status_code == 202 and response.json()["status"] == "accepted"
        enqueue.assert_awaited()

    async def test_an_event_outside_the_filter_costs_nothing(self):
        response, enqueue, remember, _ = await self._post(
            _trigger(), headers={"X-Trigger-Secret": "s3cret"}, json={"total": 100}
        )
        assert response.status_code == 202
        assert response.json()["status"] == "filtered"
        enqueue.assert_not_awaited()
        remember.assert_not_awaited()

    async def test_a_paused_trigger_still_answers_202(self):
        response, enqueue, *_ = await self._post(
            _trigger(is_active=False),
            headers={"X-Trigger-Secret": "s3cret"},
            json={"total": 9000},
        )
        assert response.status_code == 202 and response.json()["status"] == "paused"
        enqueue.assert_not_awaited()

    async def test_a_body_that_is_not_json_is_kept_not_refused(self):
        response, enqueue, *_ = await self._post(
            _trigger(filter=[]),
            headers={"X-Trigger-Secret": "s3cret", "content-type": "text/plain"},
            content=b"order 9000 for meera",
        )
        assert response.status_code == 202 and response.json()["status"] == "accepted"
        assert enqueue.await_args.args[2] == {"raw": "order 9000 for meera"}

    async def test_a_redelivery_is_not_a_second_run(self):
        from api.app import app
        from api.routes import public_triggers as route

        with (
            patch.object(
                route.db_client,
                "get_bot_trigger_by_uuid",
                AsyncMock(return_value=_trigger()),
            ),
            patch.object(route, "already_delivered", AsyncMock(return_value=0)),
            patch.object(route, "enqueue_job", AsyncMock()) as enqueue,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/public/triggers/" + _trigger().uuid,
                    headers={"X-Trigger-Secret": "s3cret"},
                    json={"event_id": "evt_1", "total": 9000},
                )
        assert response.json()["status"] == "duplicate"
        enqueue.assert_not_awaited()


class _no_session:
    """A session that is only ever passed through, never used."""

    async def __aenter__(self):
        return None

    async def __aexit__(self, *_):
        return False


@pytest.mark.asyncio
class TestTheOperatorsRoutes:
    def _override(self):
        from api.app import app
        from api.services.auth.depends import (
            get_user,
            get_user_with_selected_organization,
        )

        user = SimpleNamespace(id=1, selected_organization_id=7)
        app.dependency_overrides[get_user] = lambda: user
        app.dependency_overrides[get_user_with_selected_organization] = lambda: user
        # The ADMIN check reads the membership row; stand it in as ADMIN.
        self._membership = patch(
            "api.services.auth.depends.db_client.get_membership",
            AsyncMock(return_value=SimpleNamespace(role="admin")),
        )
        self._membership.start()
        return app

    def _clear(self, app):
        app.dependency_overrides.clear()
        self._membership.stop()

    async def test_compile_answers_with_questions_rather_than_refusing(self):
        app = self._override()
        from api.routes import bot_triggers as route

        asked = bot_triggers.Compiled(
            name="Big order",
            instruction="Message the customer.",
            questions=[{"field": "threshold", "question": "How big is big?"}],
        )
        try:
            with (
                patch.object(
                    route.db_client,
                    "get_workflow",
                    AsyncMock(return_value=SimpleNamespace(id=42)),
                ),
                patch.object(
                    route.bot_triggers, "compile", AsyncMock(return_value=asked)
                ),
                patch.object(route.db_client, "async_session", _no_session),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        "/api/v1/workflows/42/triggers/compile",
                        json={
                            "sentence": "when a big order comes in message the customer"
                        },
                    )
        finally:
            self._clear(app)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ready"] is False
        assert body["questions"][0]["field"] == "threshold"

    async def test_saving_mints_an_address_and_a_secret(self):
        app = self._override()
        from api.routes import bot_triggers as route

        captured = {}

        async def create(**fields):
            captured.update(fields)
            return _trigger(
                **{k: v for k, v in fields.items() if k in _trigger().__dict__}
            )

        try:
            with (
                patch.object(
                    route.db_client,
                    "get_workflow",
                    AsyncMock(return_value=SimpleNamespace(id=42)),
                ),
                patch.object(
                    route.db_client,
                    "bot_triggers_for_workflow",
                    AsyncMock(return_value=[]),
                ),
                patch.object(route.db_client, "create_bot_trigger", create),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        "/api/v1/workflows/42/triggers",
                        json={
                            "name": "Big order",
                            "sentence": "when a big order comes in",
                            "instruction": "Message the customer.",
                            "fields": [{"name": "phone", "required": True}],
                            "filter": [{"field": "total", "op": "gt", "value": "5000"}],
                        },
                    )
        finally:
            self._clear(app)
        assert response.status_code == 201, response.text
        assert len(captured["uuid"]) == 36 and len(captured["secret"]) >= 24
        body = response.json()
        assert body["url"].endswith("/api/v1/public/triggers/" + captured["uuid"])
        assert body["filter_summary"] == "total > 5000"

    async def test_a_test_event_outside_the_filter_is_said_not_run(self):
        app = self._override()
        from api.routes import bot_triggers as route

        try:
            with (
                patch.object(
                    route.db_client,
                    "get_workflow",
                    AsyncMock(return_value=SimpleNamespace(id=42)),
                ),
                patch.object(
                    route.db_client,
                    "get_bot_trigger",
                    AsyncMock(return_value=_trigger()),
                ),
                patch.object(route, "enqueue_job", AsyncMock()) as enqueue,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        "/api/v1/workflows/42/triggers/5/test",
                        json={"payload": {"total": 10}},
                    )
        finally:
            self._clear(app)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "filtered"
        assert response.json()["missing_fields"] == ["phone"]
        enqueue.assert_not_awaited()
