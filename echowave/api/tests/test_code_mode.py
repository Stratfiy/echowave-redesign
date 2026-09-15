"""Code Mode: a routine over many rows is one script, one charge (Step 20).

The plan's check: the receivables chaser on a 200-row sheet costs fewer
credits than before, measured on the receipt. Before, the run was 2
credits plus one tool call per row read and per message sent; now it is 2
credits plus one script run of 4, and the calls inside are not billed.
Around it: the tool is offered on a paid plan and a node with tools, never
on Free or on a call; a script reaches only the bot's own tools, by the
names the model knows; a box that never started is not charged.

Eval scenario: code_mode_receivables_chaser_costs_fewer_credits.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.billing import events as billing_events
from api.services.sandbox import code_mode, jobs
from api.services.sandbox.runner import LocalRunner

ORG = 7
ROWS = 200
#: Every fourth row on the sheet is paid up; the rest are chased.
OVERDUE = sum(1 for i in range(ROWS) if i % 4 != 3)


def _tool(slug: str, name: str, toolkit: str = "googlesheets"):
    return SimpleNamespace(
        id=1,
        tool_uuid=f"t-{name}",
        name=name,
        description="d",
        category="composio",
        definition={
            "type": "composio",
            "config": {"tool_slug": slug, "toolkit": toolkit, "parameters": []},
        },
    )


SHEET = _tool("GOOGLESHEETS_BATCH_GET", "Read the receivables sheet")
SEND = _tool("WHATSAPP_SEND_MESSAGE", "Send WhatsApp reminder", "whatsapp")


def _paid():
    return patch.object(code_mode, "allowed", AsyncMock(return_value=True))


@pytest.mark.asyncio
class TestTheReceipt:
    async def test_code_mode_receivables_chaser_costs_fewer_credits(self):
        """A 200-row sheet chased: before, one tool call per row read and
        per reminder sent; now, one script run, and the calls inside it
        cost nothing."""
        rows = [
            {
                "name": f"Customer {i}",
                "phone": f"+9198765{i:05d}",
                "due": 0 if i % 4 == 3 else 100 + i,
            }
            for i in range(ROWS)
        ]
        sent: list[str] = []

        async def execute(
            *, tool_slug, arguments, organization_id, connected_account_id=None, **_
        ):
            if tool_slug == "GOOGLESHEETS_BATCH_GET":
                return {"status": "success", "data": {"rows": rows}}
            sent.append(arguments["to"])
            return {"status": "success", "data": {"sent": True}}

        code = """
rows = tools.read_the_receivables_sheet(range="A2:C500")["data"]["rows"]
n = 0
for r in rows:
    if r["due"] > 0:
        tools.send_whatsapp_reminder(to=r["phone"], text=f"Reminder: {r['due']} due")
        n += 1
print(f"reminded {n}")
"""
        charges: list[str] = []

        async def charge(*, organization_id, event, ref_id, note):
            charges.append(event)

        with (
            _paid(),
            patch("api.services.integrations.composio.client.execute_tool", execute),
            patch.object(billing_events, "charge_in_own_session", charge),
            patch.object(
                jobs.db_client,
                "create_sandbox_job",
                AsyncMock(return_value=SimpleNamespace(id=5)),
            ),
            patch.object(jobs.db_client, "touch_sandbox_job", AsyncMock()),
            patch.object(jobs.db_client, "finish_sandbox_job", AsyncMock()),
            patch("api.services.workflow.agent_timeline.record", AsyncMock()) as record,
        ):
            told = await code_mode.run_for_bot(
                organization_id=ORG,
                code=code,
                why="chase overdue receivables",
                tools=[SHEET, SEND],
                workflow_id=3,
                workflow_run_id=44,
                ref_id="44:script:c1",
                runner=LocalRunner(),
            )

        assert told["status"] == "success", told
        assert told["output"].strip() == f"reminded {OVERDUE}"
        assert told["calls"] == OVERDUE + 1
        assert len(sent) == OVERDUE

        # The receipt: one script run, and not one tool call.
        assert charges == [billing_events.SCRIPT_RUN]
        credits = billing_events.EVENT_CREDITS
        before = (
            credits[billing_events.ROUTINE_RUN]
            + (OVERDUE + 1) * credits[billing_events.TOOL_CALL]
        )
        after = credits[billing_events.ROUTINE_RUN] + credits[billing_events.SCRIPT_RUN]
        assert (before, after) == (153, 6)
        assert after < before

        # And the timeline says what ran.
        line = record.await_args.kwargs
        assert line["summary"].startswith(f"Ran a script: {OVERDUE + 1} tool calls")
        assert line["payload"]["script"]["status"] == jobs.DONE

    async def test_a_script_reaches_only_the_bots_own_tools(self):
        async def execute(**kw):
            return {"status": "success", "data": 1}

        code = """
try:
    tools.call("app_gmail_send_email", to="x")
except RuntimeError as e:
    print("refused:", e)
print(tools.read_the_receivables_sheet()["data"])
"""
        with (
            _paid(),
            patch("api.services.integrations.composio.client.execute_tool", execute),
            patch.object(billing_events, "charge_in_own_session", AsyncMock()),
            patch.object(
                jobs.db_client,
                "create_sandbox_job",
                AsyncMock(return_value=SimpleNamespace(id=5)),
            ),
            patch.object(jobs.db_client, "touch_sandbox_job", AsyncMock()),
            patch.object(jobs.db_client, "finish_sandbox_job", AsyncMock()),
            patch("api.services.workflow.agent_timeline.record", AsyncMock()),
        ):
            told = await code_mode.run_for_bot(
                organization_id=ORG,
                code=code,
                why="x",
                tools=[SHEET],
                ref_id="r",
                runner=LocalRunner(),
            )
        assert "refused: no tool called 'app_gmail_send_email'" in told["output"]
        assert told["output"].strip().endswith("1")

    async def test_the_threads_prefixed_name_works_too(self):
        async def execute(**kw):
            return {"status": "success", "data": "ok"}

        with (
            _paid(),
            patch("api.services.integrations.composio.client.execute_tool", execute),
            patch.object(billing_events, "charge_in_own_session", AsyncMock()),
            patch.object(
                jobs.db_client,
                "create_sandbox_job",
                AsyncMock(return_value=SimpleNamespace(id=5)),
            ),
            patch.object(jobs.db_client, "touch_sandbox_job", AsyncMock()),
            patch.object(jobs.db_client, "finish_sandbox_job", AsyncMock()),
            patch("api.services.workflow.agent_timeline.record", AsyncMock()),
        ):
            told = await code_mode.run_for_bot(
                organization_id=ORG,
                code="print(tools.call('app_read_the_receivables_sheet')['data'])",
                why="x",
                tools=[SHEET],
                ref_id="r",
                runner=LocalRunner(),
            )
        assert told["output"].strip() == "ok"

    async def test_a_box_that_never_started_is_not_charged(self):
        class Dead:
            async def start(self, code, limits):
                from api.services.sandbox.runner import SandboxUnavailable

                raise SandboxUnavailable("sandbox is full; try again in a minute")

        charge = AsyncMock()
        with (
            _paid(),
            patch.object(billing_events, "charge_in_own_session", charge),
            patch.object(
                jobs.db_client,
                "create_sandbox_job",
                AsyncMock(return_value=SimpleNamespace(id=5)),
            ),
            patch.object(jobs.db_client, "finish_sandbox_job", AsyncMock()),
            patch("api.services.workflow.agent_timeline.record", AsyncMock()),
        ):
            told = await code_mode.run_for_bot(
                organization_id=ORG,
                code="print(1)",
                why="x",
                tools=[SHEET],
                ref_id="r",
                runner=Dead(),
            )
        assert told["status"] == "error" and "try again" in told["error"]
        charge.assert_not_awaited()

    async def test_a_free_account_cannot_run_one(self):
        with patch.object(code_mode, "allowed", AsyncMock(return_value=False)):
            told = await code_mode.run_for_bot(
                organization_id=ORG, code="print(1)", why="x", tools=[SHEET], ref_id="r"
            )
        assert told["status"] == "unavailable" and "Everyday" in told["reason"]


@pytest.mark.asyncio
class TestTheGate:
    async def test_free_is_not_offered_and_everyday_is(self):
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        for code, expected in (("free", False), ("everyday", True), ("business", True)):
            with (
                patch.object(code_mode, "SANDBOX_URL", "http://sandbox:8080"),
                patch.object(
                    code_mode.db_client, "async_session", return_value=session
                ),
                patch(
                    "api.services.billing.subscription_plans.plan_for_organization",
                    AsyncMock(return_value=SimpleNamespace(code=code)),
                ),
            ):
                assert await code_mode.allowed(ORG) is expected, code

    async def test_no_sandbox_anywhere_means_not_offered(self):
        with (
            patch.object(code_mode, "SANDBOX_URL", None),
            patch.object(code_mode, "_local_allowed", return_value=False),
        ):
            assert await code_mode.allowed(ORG) is False

    async def test_the_tool_is_in_the_function_list_only_when_allowed_and_there_are_tools(
        self,
    ):
        from api.services.workflow.pipecat_engine_context_composer import (
            compose_functions_for_node,
        )

        node = SimpleNamespace(
            document_uuids=None,
            tool_uuids=["u1"],
            mcp_tool_filters=None,
            out_edges=[],
            node_type="agent",
        )
        manager = SimpleNamespace(get_tool_schemas=AsyncMock(return_value=[]))
        offered = await compose_functions_for_node(
            node=node, custom_tool_manager=manager, can_run_scripts=True
        )
        assert code_mode.TOOL_NAME in [f.name for f in offered]
        withheld = await compose_functions_for_node(
            node=node, custom_tool_manager=manager, can_run_scripts=False
        )
        assert code_mode.TOOL_NAME not in [f.name for f in withheld]
        bare = SimpleNamespace(**{**node.__dict__, "tool_uuids": []})
        assert code_mode.TOOL_NAME not in [
            f.name
            for f in await compose_functions_for_node(
                node=bare, custom_tool_manager=manager, can_run_scripts=True
            )
        ]


class TestTheDescription:
    def test_it_tells_the_model_the_limits_and_the_names(self):
        text = code_mode.DESCRIPTION
        assert "200 tool calls" in text and "5 minutes" in text
        assert (
            "tools.call(" in text and "tools.spilled(" in text and "no network" in text
        )
