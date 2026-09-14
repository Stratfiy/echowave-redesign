"""A bot needs a key; a person fills a form; the chat never holds the key.

Arrival tests: the request reaches a row a screen can render, the values
reach the credential store and nowhere else, the card is stamped with a
handle, and the bot is handed that handle. The secret is asserted absent
from every row and every job argument, which is the whole point.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.enums import AgentEventKind
from api.services.workflow import secrets_request
from api.services.workflow.pipecat_engine_context_composer import (
    compose_functions_for_node,
)

SECRET = "rzp_live_9f3k2Ab7f3a"


def node():
    return SimpleNamespace(
        out_edges=[],
        document_uuids=[],
        tool_uuids=[],
        mcp_tool_filters=None,
        is_end=False,
    )


class TestWhatCanBeAsked:
    def test_each_type_has_its_form(self):
        payload = secrets_request.normalise(
            {
                "name": "Razorpay key",
                "credential_type": "api_key",
                "why": "to fetch orders",
            }
        )
        assert [f["key"] for f in payload["fields"]] == ["header_name", "api_key"]
        assert payload["fields"][1]["secret"] is True

    def test_no_name_or_an_unknown_type_is_not_a_request(self):
        assert secrets_request.normalise({"credential_type": "api_key"}) is None
        assert (
            secrets_request.normalise({"name": "x", "credential_type": "oauth2"})
            is None
        )
        assert (
            secrets_request.normalise({"name": "x", "credential_type": "none"}) is None
        )


@pytest.mark.asyncio
class TestTheRequestReachesTheTimeline:
    async def test_the_tool_is_offered_where_asking_a_person_is(self):
        offered = [
            f.name
            for f in await compose_functions_for_node(
                node=node(), custom_tool_manager=None, can_ask_for_decision=True
            )
        ]
        withheld = [
            f.name
            for f in await compose_functions_for_node(
                node=node(), custom_tool_manager=None
            )
        ]
        assert secrets_request.TOOL_NAME in offered
        assert secrets_request.TOOL_NAME not in withheld

    async def test_asking_writes_a_needs_secret_row_with_no_value_in_it(self):
        with patch(
            "api.services.workflow.secrets_request.agent_timeline.record",
            new=AsyncMock(),
        ) as record:
            result = await secrets_request.ask(
                organization_id=7,
                workflow_id=3,
                workflow_run_id=11,
                arguments={
                    "name": "Razorpay key",
                    "credential_type": "api_key",
                    "why": "orders",
                },
            )
        assert result["status"] == "asked"
        kwargs = record.await_args.kwargs
        assert kwargs["kind"] == AgentEventKind.NEEDS_SECRET.value
        assert kwargs["summary"] == "Needs Razorpay key"
        assert set(kwargs["payload"]) == {"name", "why", "credential_type", "fields"}


def _request(**overrides):
    payload = secrets_request.normalise(
        {"name": "Razorpay key", "credential_type": "api_key"}
    )
    payload.update(overrides)
    return SimpleNamespace(
        id=99,
        kind=AgentEventKind.NEEDS_SECRET.value,
        payload=payload,
        folder_id=5,
        workflow_id=3,
    )


def _stored(name="Razorpay key"):
    return SimpleNamespace(credential_uuid="cred-uuid-1", name=name)


@pytest.mark.asyncio
class TestTheValuesReachTheStoreAndNowhereElse:
    async def test_the_key_becomes_a_credential_and_the_card_gets_a_handle(self):
        with (
            patch(
                "api.services.workflow.secrets_request.db_client.get_agent_event",
                new=AsyncMock(return_value=_request()),
            ),
            patch(
                "api.services.workflow.secrets_request.db_client.create_credential",
                new=AsyncMock(return_value=_stored()),
            ) as create,
            patch(
                "api.services.workflow.secrets_request.db_client.set_agent_event_payload",
                new=AsyncMock(return_value=True),
            ) as write,
            patch(
                "api.services.workflow.secrets_request.agent_timeline.record",
                new=AsyncMock(),
            ) as record,
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as enqueue,
        ):
            payload = await secrets_request.provide(
                organization_id=7, event_id=99, values={"api_key": SECRET}, user_id=42
            )
        # The store got the value, with the header defaulted.
        stored = create.await_args.kwargs
        assert stored["organization_id"] == 7
        assert stored["credential_type"] == "api_key"
        assert stored["credential_data"] == {
            "header_name": "X-API-Key",
            "api_key": SECRET,
        }
        # The card got a handle and a hint, never the value.
        assert payload["provided"]["credential_uuid"] == "cred-uuid-1"
        assert payload["provided"]["hint"] == "7f3a"
        assert SECRET not in str(write.await_args.kwargs["payload"])
        # The channel line and the bot's next turn carry the handle only.
        message = record.await_args.kwargs
        assert message["kind"] == AgentEventKind.MESSAGE.value
        assert "cred-uuid-1" in message["summary"]
        assert SECRET not in str(message)
        assert enqueue.await_args.args[1:3] == (3, 5)
        assert SECRET not in str(enqueue.await_args)

    async def test_a_missing_secret_field_is_refused_before_anything_is_written(self):
        with (
            patch(
                "api.services.workflow.secrets_request.db_client.get_agent_event",
                new=AsyncMock(return_value=_request()),
            ),
            patch(
                "api.services.workflow.secrets_request.db_client.create_credential",
                new=AsyncMock(),
            ) as create,
        ):
            with pytest.raises(secrets_request.SecretError, match="API key is needed"):
                await secrets_request.provide(
                    organization_id=7, event_id=99, values={"api_key": "  "}, user_id=42
                )
        create.assert_not_awaited()

    async def test_a_second_submission_is_refused(self):
        with patch(
            "api.services.workflow.secrets_request.db_client.get_agent_event",
            new=AsyncMock(return_value=_request(provided={"credential_uuid": "x"})),
        ):
            with pytest.raises(secrets_request.SecretError, match="Already"):
                await secrets_request.provide(
                    organization_id=7,
                    event_id=99,
                    values={"api_key": SECRET},
                    user_id=42,
                )

    async def test_a_name_clash_gets_a_suffix_rather_than_a_failure(self):
        create = AsyncMock(
            side_effect=[
                Exception("unique_org_credential_name"),
                _stored("Razorpay key (ab12)"),
            ]
        )
        with (
            patch(
                "api.services.workflow.secrets_request.db_client.get_agent_event",
                new=AsyncMock(return_value=_request()),
            ),
            patch(
                "api.services.workflow.secrets_request.db_client.create_credential",
                new=create,
            ),
            patch(
                "api.services.workflow.secrets_request.db_client.set_agent_event_payload",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "api.services.workflow.secrets_request.agent_timeline.record",
                new=AsyncMock(),
            ),
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()),
        ):
            payload = await secrets_request.provide(
                organization_id=7, event_id=99, values={"api_key": SECRET}, user_id=42
            )
        assert create.await_count == 2
        assert payload["provided"]["credential_name"] == "Razorpay key (ab12)"
