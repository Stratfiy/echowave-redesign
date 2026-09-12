"""What happens once the call is over.

A call is finished when the record exists, not when it is answered. These tests
are about the three ways that promise breaks: an action that fires on the wrong
call, an action that quietly fires on none, and one broken step taking the rest
of the post-call pipeline with it.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.outcomes import (
    RUNNABLE_KINDS,
    actions_for,
    parse_actions,
    render_arguments,
    run_for_call,
)


def _tool(uuid="t-1", category="composio", name="Sheet", config=None):
    return SimpleNamespace(
        tool_uuid=uuid,
        name=name,
        category=category,
        definition={"type": category, "config": config or {"tool_slug": "X"}},
    )


def _config(*actions):
    return {"outcome_actions": list(actions)}


class TestWhichActionsFire:
    def test_an_action_with_no_condition_fires_on_every_call(self):
        actions = parse_actions(_config({"tool_uuid": "t1"}))
        assert actions_for(actions, "booked")
        assert actions_for(actions, "no_answer")
        assert actions_for(actions, None)

    def test_an_action_fires_only_on_the_outcomes_it_names(self):
        actions = parse_actions(_config({"tool_uuid": "t1", "when": ["booked"]}))
        assert actions_for(actions, "booked")
        assert not actions_for(actions, "no_answer")

    def test_the_code_matches_whatever_case_either_side_typed(self):
        """The code is typed by an operator in one screen and produced by a
        classifier in another. "Booked" failing to match "booked" is a no-op
        with no symptom -- precisely the bug class in api/AGENTS.md."""
        actions = parse_actions(_config({"tool_uuid": "t1", "when": ["Booked"]}))
        assert actions_for(actions, "booked")
        assert actions_for(actions, "  BOOKED ")

    def test_a_disabled_action_never_fires(self):
        actions = parse_actions(_config({"tool_uuid": "t1", "enabled": False}))
        assert actions_for(actions, "booked") == []

    def test_one_malformed_action_does_not_stop_the_others(self):
        """An operator whose CRM step is broken still wants their sheet
        written. After the call, the alternative to a partial result is none."""
        actions = parse_actions(_config({"bad": 1}, {"tool_uuid": "t2"}, "not a dict"))
        assert [a.tool_uuid for a in actions] == ["t2"]

    @pytest.mark.parametrize(
        "raw", [None, {}, {"outcome_actions": None}, {"outcome_actions": "x"}]
    )
    def test_an_agent_with_no_outcomes_configured_does_nothing(self, raw):
        assert parse_actions(raw) == []


class TestArguments:
    def test_a_template_is_filled_from_the_calls_own_context(self):
        assert render_arguments(
            {"name": "{{ gathered_context.customer_name }}"},
            {"gathered_context": {"customer_name": "Rahul"}},
        ) == {"name": "Rahul"}

    def test_a_template_that_resolves_to_nothing_is_sent_empty_not_dropped(self):
        """The provider rejecting a blank required field is a visible error in
        the interactions table. A silently absent key is a different request
        from the one the operator wrote."""
        rendered = render_arguments(
            {"name": "{{ gathered_context.missing }}"}, {"gathered_context": {}}
        )
        assert "name" in rendered
        assert rendered["name"] == ""


class TestRunning:
    @pytest.mark.asyncio
    async def test_a_fired_action_runs_and_is_recorded_against_the_run(self):
        """The recording is what makes outcome rate mean what it says: the
        promise and the metric become the same event."""
        with (
            patch(
                "api.services.workflow.outcomes.db_client.get_tools_by_uuids",
                AsyncMock(return_value=[_tool()]),
            ),
            patch(
                "api.services.workflow.outcomes.execute_composio_tool",
                AsyncMock(return_value={"status": "success"}),
            ) as run,
            patch(
                "api.services.workflow.outcomes.app_interactions.record", AsyncMock()
            ) as rec,
        ):
            succeeded = await run_for_call(
                organization_id=42,
                workflow_run_id=7,
                workflow_id=3,
                definition_id=13,
                configurations=_config({"tool_uuid": "t-1"}),
                disposition="booked",
                render_context={"gathered_context": {}},
            )

        assert succeeded == 1
        run.assert_awaited_once()
        kwargs = rec.await_args.kwargs
        assert kwargs["workflow_run_id"] == 7
        assert kwargs["definition_id"] == 13
        assert kwargs["status"] == "success"
        assert kwargs["name"].startswith("outcome:")

    @pytest.mark.asyncio
    async def test_a_failing_action_is_recorded_as_a_failure_not_swallowed(self):
        with (
            patch(
                "api.services.workflow.outcomes.db_client.get_tools_by_uuids",
                AsyncMock(return_value=[_tool()]),
            ),
            patch(
                "api.services.workflow.outcomes.execute_composio_tool",
                AsyncMock(side_effect=RuntimeError("sheet is read-only")),
            ),
            patch(
                "api.services.workflow.outcomes.app_interactions.record", AsyncMock()
            ) as rec,
        ):
            succeeded = await run_for_call(
                organization_id=42,
                workflow_run_id=7,
                workflow_id=3,
                definition_id=13,
                configurations=_config({"tool_uuid": "t-1"}),
                disposition="booked",
                render_context={},
            )

        assert succeeded == 0
        assert rec.await_args.kwargs["status"] == "error"
        assert "read-only" in rec.await_args.kwargs["error"]

    @pytest.mark.asyncio
    async def test_one_broken_step_does_not_stop_the_next(self):
        tools = [_tool("t-1"), _tool("t-2", name="CRM")]
        with (
            patch(
                "api.services.workflow.outcomes.db_client.get_tools_by_uuids",
                AsyncMock(return_value=tools),
            ),
            patch(
                "api.services.workflow.outcomes.execute_composio_tool",
                AsyncMock(side_effect=[RuntimeError("boom"), {"status": "success"}]),
            ),
            patch(
                "api.services.workflow.outcomes.app_interactions.record", AsyncMock()
            ) as rec,
        ):
            succeeded = await run_for_call(
                organization_id=42,
                workflow_run_id=7,
                workflow_id=3,
                definition_id=13,
                configurations=_config({"tool_uuid": "t-1"}, {"tool_uuid": "t-2"}),
                disposition="booked",
                render_context={},
            )

        assert succeeded == 1
        assert rec.await_count == 2

    @pytest.mark.asyncio
    async def test_a_tool_belonging_to_another_account_is_never_run(self):
        """get_tools_by_uuids is organization-scoped, so a uuid that comes back
        missing is either deleted or somebody else's. Both mean do not run it."""
        with (
            patch(
                "api.services.workflow.outcomes.db_client.get_tools_by_uuids",
                AsyncMock(return_value=[]),
            ),
            patch(
                "api.services.workflow.outcomes.execute_composio_tool", AsyncMock()
            ) as run,
        ):
            assert (
                await run_for_call(
                    organization_id=42,
                    workflow_run_id=7,
                    workflow_id=3,
                    definition_id=13,
                    configurations=_config({"tool_uuid": "someone-elses"}),
                    disposition="booked",
                    render_context={},
                )
                == 0
            )
        run.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_tool_that_makes_no_sense_after_a_call_is_refused(self):
        """Ending a call that has ended, transferring a caller who has hung up.
        Offering these would be offering a button that cannot work."""
        for category in ("end_call", "transfer_call", "calculator", "mcp"):
            assert category not in RUNNABLE_KINDS
            with (
                patch(
                    "api.services.workflow.outcomes.db_client.get_tools_by_uuids",
                    AsyncMock(return_value=[_tool(category=category)]),
                ),
                patch(
                    "api.services.workflow.outcomes.execute_http_tool", AsyncMock()
                ) as run,
            ):
                assert (
                    await run_for_call(
                        organization_id=42,
                        workflow_run_id=7,
                        workflow_id=3,
                        definition_id=13,
                        configurations=_config({"tool_uuid": "t-1"}),
                        disposition="booked",
                        render_context={},
                    )
                    == 0
                )
            run.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_run_with_no_organization_does_nothing(self):
        assert (
            await run_for_call(
                organization_id=None,
                workflow_run_id=7,
                workflow_id=3,
                definition_id=13,
                configurations=_config({"tool_uuid": "t-1"}),
                disposition="booked",
                render_context={},
            )
            == 0
        )


class TestItRunsForAnAgentThatHasNothingElse:
    def test_outcomes_are_above_the_webhook_early_return(self):
        """The trap this avoids: the post-call task returns early when an agent
        has no webhook or message nodes, and an agent whose whole configuration
        is "write it to my sheet" has neither. Below that return, outcomes would
        work only for agents that already had something else configured -- which
        nobody reports as a bug, they report that it does not work."""
        import inspect

        from api.tasks.run_integrations import run_integrations_post_workflow_run

        source = inspect.getsource(run_integrations_post_workflow_run)
        outcome_at = source.index("outcomes.run_for_call")
        early_return = source.index("No webhook or message nodes in workflow")
        assert outcome_at < early_return
