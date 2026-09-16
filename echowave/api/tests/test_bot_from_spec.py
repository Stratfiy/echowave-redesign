"""A customer's written spec becomes a bot, not a conversation about one.

Decibyl could only create a bot from one of eight templates plus named
answers. Anything a vendor actually specified -- device key presses, a
backend check before the next step, consent before a sensitive action --
is not any of those templates and cannot be bent into one, so the person
was offered the nearest template instead of the thing they asked for. The
generator that builds a whole graph from written text was already here and
reachable only from the create wizard.
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow import actions, bot_from_brief, decibyl

SPEC = (
    "Customer calls and reports the Elock is not opening. Capture the TT "
    "number and the 10-digit invoice number. Validate them against the "
    "backend. Ask them to press 6# to refresh the device. Confirm whether "
    "the GPS light is blinking. If it is, ask them to press 3# for an OTP. "
    "Check the backend for OTP status. If no OTP arrived, ask consent "
    "before triggering a manual OTP. Then 2#, the OTP, then #. Confirm the "
    "lock opened, or escalate with the whole conversation."
)


class TestWhatTheModelIsAskedFor:
    def test_decibyl_carries_the_tool(self):
        assert bot_from_brief.TOOL_NAME in {t["name"] for t in decibyl.office_tools()}

    def test_it_tells_the_model_not_to_summarise(self):
        """A spec condensed to one sentence generates a one-step bot, which
        is the failure this exists to prevent."""
        assert "do not summarise" in bot_from_brief.DESCRIPTION.lower()

    def test_it_is_not_offered_in_propose_actions_enum(self):
        assert bot_from_brief.ACTION not in actions.ACTIONS
        assert bot_from_brief.ACTION in actions.INTERNAL_ACTIONS


class TestTheProposal:
    def test_a_real_spec_resolves_to_a_card(self):
        payload = bot_from_brief.resolve(
            {
                "name": "Elock support",
                "call_type": "inbound",
                "use_case": "Elock support",
                "spec": SPEC,
            }
        )
        assert payload["action"] == bot_from_brief.ACTION
        assert payload["label"] == "Build Elock support from the spec"
        assert payload["args"]["spec"] == SPEC
        assert payload["reversible"] is False
        assert payload["state"] == "proposed"

    def test_nothing_is_generated_while_proposing(self):
        """Generation is slow and costs money. A card that spent both before
        anybody pressed anything would let a chatty model bill an account
        for bots nobody asked for."""
        with patch.object(
            bot_from_brief, "generate_workflow_definition", AsyncMock()
        ) as gen:
            bot_from_brief.resolve(
                {"name": "Elock support", "call_type": "inbound", "spec": SPEC}
            )
        gen.assert_not_awaited()

    def test_a_spec_too_thin_to_build_from_is_refused_with_a_reason(self):
        with pytest.raises(bot_from_brief.BriefError) as exc:
            bot_from_brief.resolve(
                {"name": "Thing", "call_type": "inbound", "spec": "do support"}
            )
        assert "not enough" in str(exc.value)

    def test_an_unnamed_bot_is_refused(self):
        with pytest.raises(bot_from_brief.BriefError):
            bot_from_brief.resolve({"name": "", "call_type": "inbound", "spec": SPEC})

    def test_who_starts_the_conversation_must_be_said(self):
        with pytest.raises(bot_from_brief.BriefError) as exc:
            bot_from_brief.resolve({"name": "X", "call_type": "sideways", "spec": SPEC})
        assert "inbound" in str(exc.value)

    def test_a_long_spec_is_clipped_rather_than_refused(self):
        payload = bot_from_brief.resolve(
            {"name": "X", "call_type": "inbound", "spec": "a" * 50_000}
        )
        assert len(payload["args"]["spec"]) == bot_from_brief.MAX_BRIEF_CHARS

    def test_the_use_case_falls_back_to_the_name(self):
        payload = bot_from_brief.resolve(
            {"name": "Elock support", "call_type": "inbound", "spec": SPEC}
        )
        assert payload["args"]["use_case"] == "Elock support"


class _Workflow:
    id = 42
    name = "Elock support"
    handle = "elock-support"


class TestBuilding:
    @pytest.mark.asyncio
    async def test_the_whole_spec_reaches_generation(self):
        """Not the name, not a summary: the steps are what the graph is
        built from."""
        with (
            patch.object(
                bot_from_brief,
                "generate_workflow_definition",
                AsyncMock(
                    return_value={"workflow_definition": {"nodes": [{"id": "a"}]}}
                ),
            ) as gen,
            patch.object(
                bot_from_brief.db_client,
                "create_workflow",
                AsyncMock(return_value=_Workflow()),
            ),
            patch.object(
                bot_from_brief.db_client, "assert_trigger_paths_available", AsyncMock()
            ),
        ):
            built = await bot_from_brief.build(
                organization_id=1,
                user_id=9,
                args={
                    "name": "Elock support",
                    "call_type": "inbound",
                    "use_case": "Elock support",
                    "spec": SPEC,
                },
            )
        sent = gen.await_args.kwargs["activity_description"]
        for step in ("6#", "3#", "2#", "consent", "escalate"):
            assert step in sent
        assert gen.await_args.kwargs["call_type"] == "INBOUND"
        assert gen.await_args.kwargs["organization_id"] == 1
        assert built["workflow_id"] == 42

    @pytest.mark.asyncio
    async def test_the_bot_is_created_for_the_person_who_confirmed(self):
        with (
            patch.object(
                bot_from_brief,
                "generate_workflow_definition",
                AsyncMock(return_value={"workflow_definition": {"nodes": []}}),
            ),
            patch.object(
                bot_from_brief.db_client,
                "create_workflow",
                AsyncMock(return_value=_Workflow()),
            ) as create,
            patch.object(
                bot_from_brief.db_client, "assert_trigger_paths_available", AsyncMock()
            ),
        ):
            await bot_from_brief.build(
                organization_id=7,
                user_id=9,
                args={
                    "name": "Elock support",
                    "call_type": "inbound",
                    "use_case": "support",
                    "spec": SPEC,
                },
            )
        assert create.await_args.kwargs["organization_id"] == 7
        assert create.await_args.kwargs["user_id"] == 9

    @pytest.mark.asyncio
    async def test_a_trigger_path_somebody_else_holds_does_not_lose_the_bot(self):
        with (
            patch.object(
                bot_from_brief,
                "generate_workflow_definition",
                AsyncMock(return_value={"workflow_definition": {"nodes": []}}),
            ),
            patch.object(
                bot_from_brief.db_client,
                "create_workflow",
                AsyncMock(return_value=_Workflow()),
            ),
            patch.object(
                bot_from_brief.db_client,
                "assert_trigger_paths_available",
                AsyncMock(side_effect=RuntimeError("taken")),
            ),
            patch.object(
                bot_from_brief, "extract_trigger_paths", lambda d: ["/hook/x"]
            ),
            patch.object(
                bot_from_brief.db_client, "sync_triggers_for_workflow", AsyncMock()
            ) as sync,
        ):
            built = await bot_from_brief.build(
                organization_id=1,
                user_id=9,
                args={
                    "name": "X",
                    "call_type": "inbound",
                    "use_case": "x",
                    "spec": SPEC,
                },
            )
        assert built["workflow_id"] == 42
        sync.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_generator_that_fails_is_said_on_the_card(self):
        payload = {
            "action": bot_from_brief.ACTION,
            "args": {
                "name": "X",
                "call_type": "inbound",
                "use_case": "x",
                "spec": SPEC,
            },
            "confirmed": {"by": 9},
        }
        with patch.object(
            bot_from_brief,
            "generate_workflow_definition",
            AsyncMock(side_effect=RuntimeError("MPS is down")),
        ):
            with pytest.raises(actions.ActionError) as exc:
                await actions._execute(1, payload)
        assert "could not be built" in str(exc.value)

    @pytest.mark.asyncio
    async def test_an_unconfirmed_card_builds_nothing(self):
        payload = {
            "action": bot_from_brief.ACTION,
            "args": {"name": "X", "call_type": "inbound", "spec": SPEC},
        }
        with patch.object(
            bot_from_brief, "generate_workflow_definition", AsyncMock()
        ) as gen:
            with pytest.raises(actions.ActionError):
                await actions._execute(1, payload)
        gen.assert_not_awaited()
