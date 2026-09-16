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


class TestAFileIsABrief:
    """Upload a spec, get a bot. The rule used to say the opposite."""

    def test_the_rules_send_an_attached_spec_to_the_builder(self):
        # Read through the module rather than by a relative path: the tests
        # run from api/ in CI and from the repo root locally, and a test
        # that fails on the difference tests the working directory.
        import inspect

        from api.services.workflow import decibyl

        source = inspect.getsource(decibyl)
        assert bot_from_brief.TOOL_NAME in source
        # And not back to the template path it used to hardcode.
        assert "and call create_bot; do not" not in source
        assert decibyl.ATTACHMENT_CHARS >= 24_000

    @pytest.mark.asyncio
    async def test_the_attached_block_says_it_can_be_built(self):
        from unittest.mock import AsyncMock, patch

        from api.services.workflow import decibyl

        class _Doc:
            full_text = "Step 1: greet. Step 2: take the invoice number."

        with patch.object(
            decibyl.db_client, "get_document_by_uuid", AsyncMock(return_value=_Doc())
        ):
            block = await decibyl.attached_block(
                1, [{"document_uuid": "u", "filename": "Spec.docx"}]
            )
        assert "Spec.docx" in block
        assert bot_from_brief.TOOL_NAME in block


class TestABriefThatNamesASchedule:
    """The gap the live product exposed.

    Asked to build a bot that "reads my Gmail every morning at 8am", the
    product built a live bot whose triggers list was empty. The routine
    runtime was complete -- a clock every minute, a runner, four cadences --
    and nothing created a routine from what the person said. The eight
    o'clock survived only as prose in the spec, where no clock reads it.
    """

    BRIEF = (
        "Reads my Gmail every morning at 8am and posts me a short summary of "
        "what came in overnight - who wrote, what they want, and anything "
        "urgent."
    )

    def _build(self, spec, routine=None):
        return (
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
                bot_from_brief.db_client, "assert_trigger_paths_available", AsyncMock()
            ),
            patch.object(
                bot_from_brief.db_client,
                "create_routine",
                routine or AsyncMock(),
            ),
        )

    async def _run(self, spec, routine=None):
        gen, create, paths, made = self._build(spec, routine)
        with gen, create, paths, made:
            return await bot_from_brief.build(
                organization_id=7,
                user_id=9,
                args={
                    "name": "Inbox Brief",
                    "call_type": "inbound",
                    "use_case": "Daily Gmail summary",
                    "spec": spec,
                },
            )

    @pytest.mark.asyncio
    async def test_the_bot_gets_the_routine_its_brief_described(self):
        made = AsyncMock()
        await self._run(self.BRIEF, made)
        assert made.await_count == 1
        kwargs = made.await_args.kwargs
        assert kwargs["organization_id"] == 7
        assert kwargs["cadence"] == "daily"
        assert kwargs["anchor"] == "clock"
        assert kwargs["at_minute"] == 8 * 60

    @pytest.mark.asyncio
    async def test_the_card_says_when_it_runs_and_that_it_needs_testing(self):
        """A routine cannot arm until it has been test-run. Leaving that
        unsaid leaves somebody wondering why 8am came and went."""
        done = await self._run(self.BRIEF)
        assert done["runs"] == "every day at 08:00"
        assert "every day at 08:00" in done["note"]
        assert "test" in done["note"].lower()

    @pytest.mark.asyncio
    async def test_a_brief_with_no_schedule_makes_no_routine(self):
        """The bot is built exactly as it was before this existed."""
        made = AsyncMock()
        done = await self._run(SPEC, made)
        made.assert_not_awaited()
        assert done["runs"] is None
        assert "runs" not in done["note"]

    @pytest.mark.asyncio
    async def test_a_routine_that_cannot_be_written_does_not_lose_the_bot(self):
        """The bot is the deliverable; the schedule is not worth it."""
        made = AsyncMock(side_effect=RuntimeError("db down"))
        done = await self._run(self.BRIEF, made)
        assert done["workflow_id"] is not None
        assert done["runs"] is None

    @pytest.mark.asyncio
    async def test_the_routine_is_told_what_to_do_each_run(self):
        """The instruction is the brief, so two routines can share one bot
        and differ only in what they are told each morning."""
        made = AsyncMock()
        await self._run(self.BRIEF, made)
        assert "summary" in made.await_args.kwargs["instruction"]


class TestTheCardSaysWhyItIsThere:
    """Every other card carries a reason. This one carried an empty string.

    ``propose_action`` requires ``why`` of the model and puts it on the
    card; ``create_bot`` falls back to "From the {template} template" when
    it is blank. This card hardcoded ``"why": ""`` and its tool never asked
    the model for one -- so a person was shown an irreversible build, of a
    bot they had described in prose, with the reason line empty.

    Seen live on a real card: `{"action": "build_from_spec", "label":
    "Build Morning Inbox Brief from the spec", "why": "", "reversible":
    false}`. The label says what will happen. Nothing said why, and this
    is the one card that cannot be switched back off.
    """

    def test_the_model_is_asked_for_a_reason(self):
        schema = bot_from_brief.tool_schema()
        assert "why" in schema["parameters"]["properties"]

    def test_a_given_reason_reaches_the_card(self):
        payload = bot_from_brief.resolve(
            {
                "name": "Elock support",
                "call_type": "inbound",
                "spec": SPEC,
                "why": "You described the whole flow in the vendor doc.",
            }
        )
        assert payload["why"] == "You described the whole flow in the vendor doc."

    def test_a_missing_reason_does_not_leave_the_card_blank(self):
        """A model that omits it must not produce the card this test exists
        for. The fallback names what the bot is for, which is the honest
        answer: it is being built because somebody described it."""
        payload = bot_from_brief.resolve(
            {
                "name": "Morning Inbox Brief",
                "channel": "chat",
                "use_case": "Daily Gmail summary",
                "spec": SPEC,
            }
        )
        assert payload["why"].strip()
        assert "Daily Gmail summary" in payload["why"]

    def test_the_fallback_works_without_a_use_case_either(self):
        payload = bot_from_brief.resolve(
            {"name": "Morning Inbox Brief", "channel": "chat", "spec": SPEC}
        )
        assert payload["why"].strip()

    def test_a_reason_cannot_run_away_with_the_card(self):
        payload = bot_from_brief.resolve(
            {
                "name": "Elock support",
                "call_type": "inbound",
                "spec": SPEC,
                "why": "x" * 5_000,
            }
        )
        assert len(payload["why"]) <= actions.MAX_WHY_CHARS
