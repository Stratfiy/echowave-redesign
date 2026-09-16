"""Skills reach a prompt, which they never did before.

The defect: the catalogue ships 103 skills, the install path works, the
shelf shows them, and the row recording the choice carries a comment about
"the order their prompt blocks are joined". Nothing joined them. A skill
could be installed, shown as installed, and never reach a model.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow import skill_context


def _skill(slug, title, description="Does a thing.", body="Step one. Step two."):
    return SimpleNamespace(
        slug=slug,
        title=title,
        description=description,
        skill=SimpleNamespace(body=body),
    )


BILLING = _skill(
    "customer-billing-ops",
    "Customer Billing Ops",
    "Operate billing workflows such as refunds and churn triage.",
    "## Billing procedure\nCheck the subscription first.",
)
BRAND = _skill("brand-voice", "Brand Voice", "Write in the house voice.")
CHASING = _skill("payment-chasing", "Payment Chasing", "Chase an unpaid invoice.")


class TestWhatTheQuestionInvokes:
    def test_a_skill_is_invoked_by_a_word_in_its_title(self):
        assert skill_context.named("check the billing", [BILLING, BRAND]) == [BILLING]

    def test_a_question_invoking_nothing_invokes_nothing(self):
        assert skill_context.named("how many calls today", [BILLING, BRAND]) == []

    def test_a_generic_title_word_does_not_invoke_a_skill(self):
        """Every second skill is an "Ops" or a "Review". A question saying
        the word must not pull 12 KB of procedure into the prompt."""
        assert skill_context.named("what are our ops", [BILLING]) == []

    def test_a_short_word_does_not_invoke_a_skill(self):
        assert skill_context.named("is the tax ok", [BILLING, BRAND]) == []

    def test_the_slug_matches_as_well_as_the_title(self):
        assert skill_context.named("chase that customer", [BILLING]) == [BILLING]

    def test_the_strongest_match_wins(self):
        """ "billing refund" reaches the billing skill, not whichever sorted
        first."""
        assert skill_context.named("chase the payment", [BILLING, CHASING]) == [CHASING]

    def test_no_more_than_the_cap_is_carried(self):
        found = skill_context.named(
            "customer payment chasing billing", [BILLING, CHASING]
        )
        assert len(found) == skill_context.MAX_BODIES

    def test_an_empty_question_invokes_nothing(self):
        assert skill_context.named("", [BILLING]) == []
        assert skill_context.named("billing", []) == []


class TestWhatTheModelReads:
    def test_no_skills_is_no_block_at_all(self):
        """An account with no skills is not told it has none."""
        assert skill_context.block([]) == ""

    def test_every_installed_skill_is_named(self):
        block = skill_context.block([BILLING, BRAND])
        assert "Customer Billing Ops" in block
        assert "Brand Voice" in block

    def test_an_uninvoked_skill_carries_no_body(self):
        """The whole point: four installed skills are four lines, not 48 KB."""
        block = skill_context.block([BILLING, BRAND])
        assert "Check the subscription first" not in block

    def test_the_invoked_skill_is_carried_whole(self):
        block = skill_context.block([BILLING, BRAND], [BILLING])
        assert "Check the subscription first" in block
        assert "in full" in block

    def test_a_long_body_is_trimmed(self):
        big = _skill("big", "Big Thing", body="x" * 50_000)
        block = skill_context.block([big], [big])
        assert len(block) < skill_context.MAX_BODY_CHARS + 500

    def test_a_long_description_is_trimmed_on_its_line(self):
        wordy = _skill("wordy", "Wordy Thing", description="y" * 900)
        assert len(skill_context.block([wordy])) < 400

    def test_a_skill_with_no_body_is_skipped_not_rendered_empty(self):
        empty = _skill("empty", "Empty Thing", body="")
        block = skill_context.block([empty], [empty])
        assert "in full" not in block
        assert "Empty Thing" in block


class TestReadingTheShelf:
    @pytest.mark.asyncio
    async def test_a_shelf_that_cannot_be_read_is_a_prompt_without_one(self):
        with patch.object(
            skill_context.db_client,
            "list_organisation_skills",
            AsyncMock(side_effect=RuntimeError("down")),
        ):
            assert await skill_context.installed_for(1) == []

    @pytest.mark.asyncio
    async def test_a_withdrawn_skill_is_skipped_rather_than_blank(self):
        """`slug` is deliberately not a foreign key, so a skill withdrawn
        from a release leaves the row alone. A prompt is not where anyone
        should find that out."""
        rows = [SimpleNamespace(slug="brand-voice"), SimpleNamespace(slug="gone-away")]
        with (
            patch.object(
                skill_context.db_client,
                "list_organisation_skills",
                AsyncMock(return_value=rows),
            ),
            patch.object(
                skill_context.catalogue,
                "get",
                lambda slug: BRAND if slug == "brand-voice" else None,
            ),
        ):
            found = await skill_context.installed_for(1)
        assert [s.slug for s in found] == ["brand-voice"]

    @pytest.mark.asyncio
    async def test_the_same_skill_on_two_bots_is_read_once(self):
        rows = [
            SimpleNamespace(slug="brand-voice"),
            SimpleNamespace(slug="brand-voice"),
        ]
        with (
            patch.object(
                skill_context.db_client,
                "list_organisation_skills",
                AsyncMock(return_value=rows),
            ),
            patch.object(skill_context.catalogue, "get", lambda slug: BRAND),
        ):
            assert len(await skill_context.installed_for(1)) == 1


class TestAgainstTheRealCatalogue:
    def test_a_real_skill_is_invoked_by_its_own_name(self):
        from api.services.skills import catalogue

        billing = catalogue.get("customer-billing-ops")
        assert billing is not None
        assert skill_context.named("help with a billing refund", [billing]) == [billing]

    def test_the_real_body_is_bounded(self):
        from api.services.skills import catalogue

        skills = list(catalogue.all_skills().values())[:5]
        block = skill_context.block(skills, skills[:1])
        assert len(block) < skill_context.MAX_BODY_CHARS + 2_000


class TestTheBudget:
    """What every body in one prompt may cost, together.

    The per-skill cap bounds one block; this bounds the section. A bot with
    four procedures attached is the case the per-skill cap does not catch.
    """

    def test_bodies_stop_when_the_budget_is_spent(self):
        many = [_skill(f"s{n}", f"Thing{n}", body="x" * 6_000) for n in range(6)]
        block = skill_context.block(many, many, budget=8_000)
        assert block.count("in full") == 2

    def test_a_procedure_is_never_carried_as_a_fragment(self):
        """Half a procedure is worse than none: a model follows it to where
        it stops. So the last skill gets nothing rather than a sliver."""
        many = [_skill(f"s{n}", f"Thing{n}", body="x" * 5_900) for n in range(3)]
        block = skill_context.block(many, many, budget=6_000)
        assert block.count("in full") == 1

    def test_the_index_lines_are_never_dropped_for_budget(self):
        """A skill that lost its body is still a skill the model should
        know exists. The lines are the cheap half and are never the thing
        cut to save room."""
        many = [_skill(f"s{n}", f"Thing{n}", body="x" * 9_000) for n in range(4)]
        block = skill_context.block(many, many, budget=100)
        for n in range(4):
            assert f"Thing{n}" in block
        assert "in full" not in block

    def test_a_budget_under_the_floor_carries_no_body_at_all(self):
        """Truncation is accepted by design -- MAX_BODY_CHARS already takes
        6,000 of the largest skill's 31,000. The floor only rules out the
        degenerate case, where what is left is a heading and a sliver."""
        one = _skill("s", "Thing", body="x" * 9_000)
        assert "in full" not in skill_context.block([one], [one], budget=100)
        assert "in full" in skill_context.block([one], [one], budget=1_000)


class TestTheVoicePromptCarriesThem:
    """On a call the operator's choice to attach the skill is the referral.

    There is no question yet to match against, and a procedure somebody put
    on this bot was meant to be followed, not to be available.
    """

    def test_a_bot_skill_reaches_the_composed_prompt(self):
        from api.services.workflow.pipecat_engine_context_composer import (
            compose_system_prompt_for_node,
        )

        node = SimpleNamespace(
            prompt="Answer the phone.",
            add_global_prompt=False,
            document_uuids=[],
            id="n1",
            node_type=None,
        )
        workflow = SimpleNamespace(global_node_id=None, nodes={}, edges=[])
        prompt = compose_system_prompt_for_node(
            node=node,
            workflow=workflow,
            format_prompt=lambda p: p,
            has_recordings=False,
            skills=skill_context.block([BILLING], [BILLING]),
        )
        assert "Customer Billing Ops" in prompt
        assert "Check the subscription first" in prompt

    def test_a_bot_with_no_skills_gets_no_block(self):
        from api.services.workflow.pipecat_engine_context_composer import (
            compose_system_prompt_for_node,
        )

        node = SimpleNamespace(
            prompt="Answer the phone.",
            add_global_prompt=False,
            document_uuids=[],
            id="n1",
            node_type=None,
        )
        workflow = SimpleNamespace(global_node_id=None, nodes={}, edges=[])
        prompt = compose_system_prompt_for_node(
            node=node,
            workflow=workflow,
            format_prompt=lambda p: p,
            has_recordings=False,
            skills=skill_context.block([]),
        )
        assert "in full" not in prompt

    def test_a_skill_never_outranks_the_honesty_rules(self):
        """A procedure is an instruction. It is not a licence to claim an
        action that did not happen."""
        from api.services.workflow.pipecat_engine_context_composer import (
            action_honesty_instructions,
            compose_system_prompt_for_node,
        )

        node = SimpleNamespace(
            prompt="Answer the phone.",
            add_global_prompt=False,
            document_uuids=[],
            id="n1",
            node_type=None,
        )
        workflow = SimpleNamespace(global_node_id=None, nodes={}, edges=[])
        prompt = compose_system_prompt_for_node(
            node=node,
            workflow=workflow,
            format_prompt=lambda p: p,
            has_recordings=False,
            skills=skill_context.block([BILLING], [BILLING]),
        )
        honesty = action_honesty_instructions().splitlines()[0]
        assert prompt.index(honesty) < prompt.index("Customer Billing Ops")
