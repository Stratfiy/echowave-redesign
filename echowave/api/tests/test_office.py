"""The office (KAN-140): the addressee decides, and every verb ends in a card.

Arrival tests. A line that opens with a handle reaches that bot and nobody
else; a handle later in the line reaches Decibyl as a subject and wakes no
bot; a build with an answer missing becomes a question, not a card; an
edit Decibyl proposes lands on Decibyl's thread and still publishes the
right bot's draft; a test verb is a card with a link into the tester.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.enums import AgentEventKind
from api.services.workflow import actions, decibyl, office, self_edit
from api.tasks.function_names import FunctionNames


def _bot(id: int, name: str, handle: str):
    return SimpleNamespace(id=id, name=name, handle=handle)


ROSTER = [
    {"id": 3, "handle": "reception", "name": "Front desk"},
    {"id": 4, "handle": "retention", "name": "Retention"},
]


class TestTheLeadingHandleRule:
    def test_a_line_that_opens_with_a_handle_is_for_that_bot(self):
        who = office.addressee("@reception book Mrs Lakshmi for Tuesday", ROSTER)
        assert who.to == "bot"
        assert who.leading.workflow_id == 3
        assert who.subjects == []

    def test_a_handle_later_in_the_line_is_a_subject_not_an_addressee(self):
        who = office.addressee("Decibyl, edit @reception: be shorter", ROSTER)
        assert who.to == "decibyl"
        assert who.leading is None
        assert [m.workflow_id for m in who.subjects] == [3]

    def test_a_leading_handle_with_punctuation_still_leads(self):
        who = office.addressee("@reception, what are your hours?", ROSTER)
        assert who.to == "bot" and who.leading.workflow_id == 3

    def test_two_handles_the_first_leads_the_second_is_a_subject(self):
        who = office.addressee("@reception hand the lead to @retention", ROSTER)
        assert who.leading.workflow_id == 3
        assert [m.workflow_id for m in who.subjects] == [4]

    def test_an_unknown_handle_is_reported_not_guessed(self):
        who = office.addressee("@nobody do a thing", ROSTER)
        assert who.to == "decibyl" and who.unknown == ["nobody"]


@pytest.mark.asyncio
class TestDecibylRoutes:
    async def test_only_the_leading_handle_is_handed_off(self):
        with (
            patch(
                "api.services.workflow.decibyl.db_client.get_all_workflows_for_listing",
                new=AsyncMock(
                    return_value=[
                        _bot(3, "Front desk", "reception"),
                        _bot(4, "Retention", "retention"),
                    ]
                ),
            ),
            patch(
                "api.services.workflow.decibyl.agent_timeline.record", new=AsyncMock()
            ) as record,
            patch(
                "api.services.workflow.decibyl.agent_timeline.record_activity",
                new=AsyncMock(),
            ),
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as enqueue,
        ):
            asked = await decibyl.ask(
                organization_id=7,
                user_id=42,
                text="edit @reception: greet in Tamil first",
                attachments=[],
                line="edit @reception: greet in Tamil first",
                preset=None,
            )
        assert asked == []
        # Nobody was woken: the only job is Decibyl's own answer, and it
        # carries the subject.
        names = [c.args[0] for c in enqueue.await_args_list]
        assert names == [FunctionNames.ANSWER_DECIBYL_MESSAGE]
        assert enqueue.await_args.args[5] == [3]
        assert record.await_args.kwargs["payload"]["subjects"] == [3]

    async def test_a_leading_handle_still_reaches_the_bot(self):
        with (
            patch(
                "api.services.workflow.decibyl.db_client.get_all_workflows_for_listing",
                new=AsyncMock(return_value=[_bot(3, "Front desk", "reception")]),
            ),
            patch(
                "api.services.workflow.decibyl.agent_timeline.record", new=AsyncMock()
            ),
            patch(
                "api.services.workflow.decibyl.agent_timeline.record_activity",
                new=AsyncMock(),
            ),
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as enqueue,
        ):
            asked = await decibyl.ask(
                organization_id=7,
                user_id=42,
                text="@reception what are your hours?",
                attachments=[],
                line="@reception what are your hours?",
                preset=None,
            )
        assert asked == [3]
        names = [c.args[0] for c in enqueue.await_args_list]
        assert names[0] == FunctionNames.ANSWER_CHANNEL_MESSAGE
        assert enqueue.await_args_list[0].args[1] == 3


def _template(id="clinic_front_desk", needs=("clinic_name", "hours")):
    return SimpleNamespace(
        id=id,
        name="Clinic front desk",
        summary="Answers the phone for a clinic",
        direction=SimpleNamespace(value="inbound"),
        template_variables={k: f"the {k.replace('_', ' ')}" for k in needs},
    )


@pytest.mark.asyncio
class TestTheBuildCard:
    async def test_a_missing_answer_is_a_question_not_a_card(self):
        built = SimpleNamespace(
            name="Narayani front desk", definition={}, missing_variables=["hours"]
        )
        with (
            patch(
                "api.services.agent_templates.get_template",
                return_value=_template(),
            ),
            patch("api.services.agent_builder.assemble.assemble", return_value=built),
            pytest.raises(actions.ActionError, match="Ask the person.*hours"),
        ):
            await actions.resolve(
                organization_id=7,
                workflow_id=None,
                arguments={
                    "action": "create_bot",
                    "template_id": "clinic_front_desk",
                    "name": "Narayani front desk",
                    "variables": {"clinic_name": "Narayani Dental"},
                    "why": "asked",
                },
            )

    async def test_a_complete_build_is_a_card_with_the_answers_on_it(self):
        built = SimpleNamespace(
            name="Narayani front desk", definition={}, missing_variables=[]
        )
        with (
            patch(
                "api.services.agent_templates.get_template",
                return_value=_template(),
            ),
            patch("api.services.agent_builder.assemble.assemble", return_value=built),
        ):
            payload = await actions.resolve(
                organization_id=7,
                workflow_id=None,
                arguments={
                    "action": "create_bot",
                    "template_id": "clinic_front_desk",
                    "name": "Narayani front desk",
                    "variables": {"clinic_name": "Narayani Dental", "hours": "9-6"},
                    "why": "asked",
                },
            )
        assert payload["label"] == "Create Narayani front desk"
        assert payload["args"]["variables"]["hours"] == "9-6"
        assert payload["reversible"] is False
        assert payload["state"] == actions.PROPOSED

    async def test_confirm_creates_the_bot_and_the_card_learns_its_handle(self):
        created = {
            "created": True,
            "workflow_id": 99,
            "name": "Narayani front desk",
            "open_url": "/workflow/99",
        }
        payload = {
            "action": "create_bot",
            "args": {
                "template_id": "clinic_front_desk",
                "name": "Narayani front desk",
                "variables": {"clinic_name": "Narayani Dental", "hours": "9-6"},
            },
            "confirmed": {"by": 42},
        }
        with (
            patch(
                "api.services.agent_builder.tools._create_agent",
                new=AsyncMock(return_value=created),
            ) as create,
            patch(
                "api.services.workflow.actions.db_client.get_workflow",
                new=AsyncMock(
                    return_value=_bot(99, "Narayani front desk", "narayani-front-desk")
                ),
            ),
        ):
            note = await actions._execute(7, payload)
        assert create.await_args.kwargs["user_id"] == 42
        assert create.await_args.kwargs["organization_id"] == 7
        assert "@narayani-front-desk" in note
        assert payload["result"]["workflow_id"] == 99
        assert payload["result"]["open_url"] == "/workflow/99"

    async def test_nobody_confirmed_means_nobody_owns_it(self):
        with pytest.raises(actions.ActionError, match="Nobody confirmed"):
            await actions._execute(
                7, {"action": "create_bot", "args": {"template_id": "x", "name": "y"}}
            )


def _workflow_with_steps():
    return SimpleNamespace(
        id=3,
        name="Front desk",
        handle="reception",
        workflow_definition={
            "nodes": [
                {
                    "id": "n1",
                    "type": "start",
                    "data": {"name": "Greeting", "prompt": "Hello, welcome."},
                },
            ],
            "edges": [],
        },
    )


@pytest.mark.asyncio
class TestTheEditCard:
    async def test_decibyls_edit_lands_on_its_own_thread_naming_the_bot(self):
        draft = SimpleNamespace(version_number=4)
        with (
            patch(
                "api.services.workflow.office.db_client.get_all_workflows_for_listing",
                new=AsyncMock(return_value=[_bot(3, "Front desk", "reception")]),
            ),
            patch(
                "api.services.workflow.self_edit.db_client.get_workflow_by_id",
                new=AsyncMock(return_value=_workflow_with_steps()),
            ),
            patch(
                "api.services.workflow.self_edit.db_client.save_workflow_draft",
                new=AsyncMock(return_value=draft),
            ),
            patch(
                "api.services.workflow.self_edit.agent_timeline.record", new=AsyncMock()
            ) as record,
        ):
            result = await office.propose_edit(
                organization_id=7,
                arguments={
                    "bot": "@reception",
                    "step": "Greeting",
                    "new_prompt": "Vanakkam, welcome.",
                    "why": "Tamil first",
                },
            )
        assert result["status"] == "proposed"
        row = record.await_args.kwargs
        assert row["kind"] == AgentEventKind.EDIT_PROPOSED.value
        assert row["workflow_id"] is None and row["in_channel"] is False
        assert row["payload"]["workflow_id"] == 3
        assert row["payload"]["bot_name"] == "Front desk"
        assert "Front desk" in row["summary"]

    async def test_publish_on_that_card_publishes_the_named_bots_draft(self):
        event = SimpleNamespace(
            id=55,
            kind=AgentEventKind.EDIT_PROPOSED.value,
            workflow_id=None,
            payload={"workflow_id": 3, "step": "Greeting"},
        )
        with (
            patch(
                "api.services.workflow.self_edit.db_client.get_agent_event",
                new=AsyncMock(return_value=event),
            ),
            patch(
                "api.services.workflow.self_edit.db_client.publish_workflow_draft",
                new=AsyncMock(),
            ) as publish,
            patch(
                "api.services.workflow.self_edit.db_client.set_agent_event_payload",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "api.services.workflow.self_edit.agent_timeline.record", new=AsyncMock()
            ),
        ):
            payload = await self_edit.settle(
                organization_id=7, event_id=55, action="publish", user_id=42
            )
        publish.assert_awaited_once_with(3)
        assert payload["decided"]["action"] == "publish"

    async def test_an_unknown_bot_is_said_not_guessed(self):
        with patch(
            "api.services.workflow.office.db_client.get_all_workflows_for_listing",
            new=AsyncMock(return_value=[_bot(3, "Front desk", "reception")]),
        ):
            result = await office.propose_edit(
                organization_id=7,
                arguments={"bot": "@sales", "step": "Greeting", "new_prompt": "x"},
            )
        assert result["status"] == "not_proposed" and "@handle" in result["reason"]


@pytest.mark.asyncio
class TestTheTestVerbs:
    async def test_hear_it_is_a_card_with_a_link_into_the_tester(self):
        with (
            patch(
                "api.services.workflow.office.db_client.get_all_workflows_for_listing",
                new=AsyncMock(return_value=[_bot(3, "Front desk", "reception")]),
            ),
            patch(
                "api.services.workflow.office.agent_timeline.record", new=AsyncMock()
            ) as record,
        ):
            result = await office.offer_test(
                organization_id=7,
                arguments={
                    "bot": "reception",
                    "how": "hear",
                    "brief": "Saturday hours",
                },
            )
        assert result["status"] == "offered"
        row = record.await_args.kwargs
        assert row["in_channel"] is False
        assert row["payload"]["test"]["url"] == "/workflow/3?test=call"
        assert row["payload"]["test"]["how"] == "hear"
        assert "Saturday hours" in row["summary"]

    async def test_try_it_opens_text(self):
        assert office.test_url(3, "try") == "/workflow/3?test=text"

    def test_decibyl_carries_all_three_tools(self):
        names = [t["name"] for t in decibyl.TOOLS()]
        assert names == ["propose_action", "propose_edit", "test_bot"]
        assert "create_bot" in actions.ACTIONS
        assert AgentEventKind.EDIT_PROPOSED.value in decibyl.thread_filter()["kinds"]


class TestTheTemplatesBlock:
    def test_each_template_says_what_it_needs(self):
        with (
            patch(
                "api.services.agent_templates.list_templates",
                return_value=[_template()],
            ),
            patch(
                "api.services.agent_builder.assemble.required_variables",
                return_value=["clinic_name", "hours"],
            ),
        ):
            block = office.templates_block()
        assert "clinic_front_desk" in block
        assert "Needs: clinic_name (the clinic name), hours (the hours)" in block
