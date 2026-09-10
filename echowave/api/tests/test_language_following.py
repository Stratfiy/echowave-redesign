"""Whether an agent follows the caller into another language.

This replaced an environment variable, which made following a property of the
server rather than of the agent — so an account could have it on its clinic
line or on its compliance line, but not one and not the other.
"""

import pytest

from api.db.models import OrganizationModel, UserModel
from api.services.pipecat.language_following import (
    CONFIG_KEY,
    should_follow_caller_language,
)


@pytest.fixture
async def org_and_user(async_session):
    """An organization and a user to hang a created agent off."""
    org = OrganizationModel(provider_id="test-org-language-following")
    async_session.add(org)
    await async_session.flush()

    user = UserModel(
        provider_id="test-user-language-following", selected_organization_id=org.id
    )
    async_session.add(user)
    await async_session.flush()

    return org, user


class TestTheAgentDecides:
    def test_on_when_turned_on(self):
        assert should_follow_caller_language({CONFIG_KEY: True}) is True

    def test_off_when_turned_off(self):
        assert should_follow_caller_language({CONFIG_KEY: False}) is False

    @pytest.mark.parametrize("configs", [{}, {CONFIG_KEY: None}, None, "nonsense", 7])
    def test_off_until_somebody_turns_it_on(self, configs):
        """An agent with nothing stored keeps behaving as it did.

        The original reason for this default was that nothing followed anyway.
        That is no longer true -- the model is told as well as the voice -- but
        the default still cannot move: it governs every agent already running,
        and flipping it would change their calls in one deploy on a judgement
        nobody made per agent. What a *new* agent starts as is a separate
        question, answered by new_agent_workflow_configurations.
        """
        assert should_follow_caller_language(configs) is False

    @pytest.mark.parametrize("raw,expected", [(1, True), (0, False), ("", False)])
    def test_reads_what_an_older_client_stored(self, raw, expected):
        assert should_follow_caller_language({CONFIG_KEY: raw}) is expected


class TestRealtimeIsAlwaysOff:
    @pytest.mark.parametrize("configs", [{CONFIG_KEY: True}, {}, None])
    def test_whatever_anybody_configured(self, configs):
        """A speech-to-speech model answers in the language it hears.

        There are no transcription frames to watch and no TTS settings to push,
        so following would do nothing — and pinning a language on one would
        make it worse at exactly what it is already good at.
        """
        assert should_follow_caller_language(configs, is_realtime=True) is False


class TestWhatANewAgentStartsAs:
    """A field default and a starting configuration are different questions.

    The first governs agents already running and has to stay conservative. The
    second is what an agent created today is set up as, and for an India-first
    product it should ship able to follow the caller.
    """

    def test_a_new_agent_follows_the_caller(self):
        from api.schemas.workflow_configurations import (
            new_agent_workflow_configurations,
        )

        assert new_agent_workflow_configurations()[CONFIG_KEY] is True
        assert should_follow_caller_language(new_agent_workflow_configurations())

    def test_an_existing_agent_is_not_touched_by_that(self):
        """The whole point of writing it explicitly rather than moving the
        field default: an agent with nothing stored still reads as off."""
        from api.schemas.workflow_configurations import (
            WorkflowConfigurationDefaults,
        )

        assert WorkflowConfigurationDefaults().follow_caller_language is False
        assert should_follow_caller_language({}) is False

    async def test_a_created_agent_carries_it_into_its_first_version(
        self, db_session, org_and_user
    ):
        """It has to reach the definition the pipeline actually reads, not just
        the workflow row."""
        org, user = org_and_user

        workflow = await db_session.create_workflow(
            name="Brand new agent",
            workflow_definition={"nodes": [], "edges": []},
            user_id=user.id,
            organization_id=org.id,
        )

        assert workflow.workflow_configurations[CONFIG_KEY] is True
        versions = await db_session.get_workflow_versions(workflow.id)
        assert versions[0].workflow_configurations[CONFIG_KEY] is True

    async def test_a_stated_configuration_still_wins(self, db_session, org_and_user):
        """Duplicating an agent copies the original's settings; it must not
        pick up today's opinion instead."""
        org, user = org_and_user

        workflow = await db_session.create_workflow(
            name="Copy of something older",
            workflow_definition={"nodes": [], "edges": []},
            user_id=user.id,
            organization_id=org.id,
            workflow_configurations={CONFIG_KEY: False},
        )

        assert workflow.workflow_configurations[CONFIG_KEY] is False


class TestOnlyTheLanguagesThisAgentSpeaks:
    """One bad guess must not talk an agent out of its own language list.

    Recognition guesses the language of every utterance and gets short ones
    wrong. That was survivable while a wrong guess only changed the voice. Once
    the model is told as well, a clinic whose prompt lists Tamil, Kannada,
    English and Hindi got handed "the caller has switched to Telugu, reply only
    in Telugu" — later and more specific than the operator's own list, so it
    won. The caller was speaking Tamil.
    """

    @staticmethod
    def _switch_frames(follower):
        """Everything the follower pushes to act on a switch, voice and model."""
        from pipecat.frames.frames import (
            LLMMessagesAppendFrame,
            TTSUpdateSettingsFrame,
        )

        return [
            call.args[0]
            for call in follower.push_frame.await_args_list
            if isinstance(
                call.args[0], (TTSUpdateSettingsFrame, LLMMessagesAppendFrame)
            )
        ]

    def _follower(self, allowed):
        from unittest.mock import AsyncMock

        from api.services.pipecat.language_follower import LanguageFollower

        follower = LanguageFollower(initial_language="ta", allowed=allowed)
        follower.push_frame = AsyncMock()
        return follower

    async def _hear(self, follower, language, times):
        from pipecat.frames.frames import TranscriptionFrame
        from pipecat.processors.frame_processor import FrameDirection

        from api.services.pipecat.language_follower import (
            CONFIRMATIONS_BEFORE_SWITCH,  # noqa: F401  (documents the count)
        )

        text = "a sentence long enough to clear the short-utterance floor"
        for _ in range(times):
            await follower.process_frame(
                TranscriptionFrame(
                    text=text, user_id="caller", timestamp="", language=language
                ),
                FrameDirection.DOWNSTREAM,
            )

    async def test_a_language_the_agent_does_not_speak_is_ignored(self):
        from api.services.pipecat.language_follower import CONFIRMATIONS_BEFORE_SWITCH

        follower = self._follower(frozenset({"ta", "kn", "en", "hi"}))

        await self._hear(follower, "te", CONFIRMATIONS_BEFORE_SWITCH + 2)

        # The transcription itself is always forwarded — this processor
        # observes, and must never be the reason a turn fails to reach the
        # model. What must not appear is a switch.
        assert self._switch_frames(follower) == []
        assert follower.current_language == "ta"

    async def test_it_is_still_recorded_as_heard(self):
        """Worth knowing when a caller complains, even though it is not acted
        on: it says recognition thought it heard Telugu."""
        follower = self._follower(frozenset({"ta", "en"}))

        await self._hear(follower, "te", 3)

        assert "te" in follower.languages_heard

    async def test_a_language_the_agent_does_speak_still_switches(self):
        from api.services.pipecat.language_follower import CONFIRMATIONS_BEFORE_SWITCH

        follower = self._follower(frozenset({"ta", "kn", "en", "hi"}))

        await self._hear(follower, "kn", CONFIRMATIONS_BEFORE_SWITCH)

        assert follower.current_language == "kn"

    async def test_noise_between_real_turns_does_not_derail_a_switch(self):
        """The ignored detection must not count towards anything, or a caller
        alternating between a real language and a mis-detection would never
        reach the confirmations a genuine switch needs."""
        from api.services.pipecat.language_follower import CONFIRMATIONS_BEFORE_SWITCH

        follower = self._follower(frozenset({"ta", "hi"}))

        for _ in range(CONFIRMATIONS_BEFORE_SWITCH):
            await self._hear(follower, "hi", 1)
            await self._hear(follower, "te", 1)

        assert follower.current_language == "hi"

    async def test_declaring_nothing_allows_anything(self):
        """Every agent that has not declared a set behaves exactly as before."""
        from api.services.pipecat.language_follower import CONFIRMATIONS_BEFORE_SWITCH

        follower = self._follower(None)

        await self._hear(follower, "te", CONFIRMATIONS_BEFORE_SWITCH)

        assert follower.current_language == "te"


class TestReadingTheDeclaredList:
    def test_tags_are_reduced_to_the_language(self):
        from api.services.pipecat.language_following import allowed_languages

        assert allowed_languages({"agent_languages": ["ta-IN", "en-US", "hi"]}) == (
            frozenset({"ta", "en", "hi"})
        )

    @pytest.mark.parametrize(
        "configs",
        [
            {},
            None,
            "nonsense",
            {"agent_languages": []},
            {"agent_languages": ["", "  "]},
        ],
    )
    def test_nothing_declared_means_no_restriction(self, configs):
        """An empty list must never read as "this agent may speak nothing",
        which would leave it unable to follow anyone."""
        from api.services.pipecat.language_following import allowed_languages

        assert allowed_languages(configs) is None
