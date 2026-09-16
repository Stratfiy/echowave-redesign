"""A bot that never touches a phone.

The text runner has always built the same ``WorkflowGraph`` the voice
pipeline does, so a chat bot was never a runtime problem. It was that
nothing let a bot *say* it was one, so every generated bot opened with
"thank you for calling" and was told it could be interrupted mid-sentence.

These tests pin the three places that goes wrong: the default has to keep
meaning voice or a hundred live agents come off the phone; the generator has
to stop writing call words; and MPS -- which takes a call type and returns a
call -- must not be asked for a chat bot at all.
"""

import pytest

from api.enums import BotChannel
from api.schemas.workflow_configurations import (
    WorkflowConfigurationDefaults,
    channel_of,
    is_chat,
    preserve_channel,
)
from api.services.workflow import bot_from_brief, liveness
from api.services.workflow.template_generation import build_starter_workflow


def node(definition: dict, node_id: str) -> dict:
    return next(n for n in definition["nodes"] if n["id"] == node_id)


class TestWhatAnUnsetChannelMeans:
    """Every bot that existed before this field did was a call bot."""

    def test_a_configuration_block_with_no_channel_is_voice(self):
        assert channel_of({}) is BotChannel.VOICE

    def test_the_schema_default_is_voice(self):
        assert WorkflowConfigurationDefaults().channel is BotChannel.VOICE

    def test_a_missing_configuration_block_is_voice_not_a_crash(self):
        assert channel_of(None) is BotChannel.VOICE

    def test_an_unreadable_value_is_voice_rather_than_an_exception(self):
        # This comes out of a JSON column. "Is this bot on the phone" must
        # never raise on a page that was rendering a heading.
        assert channel_of({"channel": "carrier-pigeon"}) is BotChannel.VOICE
        assert channel_of({"channel": 7}) is BotChannel.VOICE

    def test_a_chat_bot_says_so(self):
        assert channel_of({"channel": "chat"}) is BotChannel.CHAT
        assert is_chat({"channel": "chat"})

    def test_is_chat_is_false_for_everything_else(self):
        assert not is_chat({})
        assert not is_chat({"channel": "voice"})


class TestTheGeneratorStopsWritingCallWords:
    def test_a_chat_bot_does_not_thank_anybody_for_calling(self):
        built = build_starter_workflow(
            "INBOUND", "Support", "Answer support questions", channel=BotChannel.CHAT
        )
        greeting = node(built["workflow_definition"], "start-1")["data"]["greeting"]
        assert "calling" not in greeting.lower()

    def test_a_voice_bot_still_does(self):
        built = build_starter_workflow("INBOUND", "Support", "Answer questions")
        greeting = node(built["workflow_definition"], "start-1")["data"]["greeting"]
        assert "calling" in greeting.lower()

    def test_a_chat_bot_is_not_told_it_can_be_interrupted(self):
        # Interruption is a fact about speech. A message is sent whole.
        built = build_starter_workflow(
            "INBOUND", "Support", "Answer questions", channel=BotChannel.CHAT
        )
        assert (
            node(built["workflow_definition"], "start-1")["data"]["allow_interrupt"]
            is False
        )

    def test_a_voice_bot_still_is(self):
        built = build_starter_workflow("INBOUND", "Support", "Answer questions")
        assert node(built["workflow_definition"], "start-1")["data"]["allow_interrupt"]

    def test_a_chat_bot_does_not_say_goodbye_for_their_time_on_the_phone(self):
        built = build_starter_workflow(
            "OUTBOUND", "Support", "Answer questions", channel=BotChannel.CHAT
        )
        prompt = node(built["workflow_definition"], "end-1")["data"]["prompt"]
        assert "goodbye" not in prompt.lower()

    def test_a_chat_bot_has_no_direction_in_its_name(self):
        # Outbound is meaningless without a phone, and a bot called
        # "Support - Outbound" that cannot ring anybody is a lie in the list.
        built = build_starter_workflow(
            "OUTBOUND", "Support", "Answer questions", channel=BotChannel.CHAT
        )
        assert built["name"].endswith("Chat")

    def test_a_voice_bot_keeps_its_direction(self):
        built = build_starter_workflow("OUTBOUND", "Support", "Answer questions")
        assert built["name"].endswith("Outbound")

    def test_a_chat_bot_ignores_an_outbound_call_type_entirely(self):
        chat_in = build_starter_workflow(
            "INBOUND", "Support", "Answer questions", channel=BotChannel.CHAT
        )
        chat_out = build_starter_workflow(
            "OUTBOUND", "Support", "Answer questions", channel=BotChannel.CHAT
        )
        assert chat_in == chat_out

    def test_the_graph_is_still_a_working_graph(self):
        # Same node set, same edges. The channel changes the words, not the
        # shape -- the text runner walks this exactly as the pipeline does.
        chat = build_starter_workflow(
            "INBOUND", "Support", "Answer questions", channel=BotChannel.CHAT
        )["workflow_definition"]
        voice = build_starter_workflow("INBOUND", "Support", "Answer questions")[
            "workflow_definition"
        ]
        assert [n["id"] for n in chat["nodes"]] == [n["id"] for n in voice["nodes"]]
        assert [e["id"] for e in chat["edges"]] == [e["id"] for e in voice["edges"]]


class TestBuildingAChatBotFromASpec:
    SPEC = (
        "When somebody opens a chat, ask which order they are asking about, "
        "look it up, tell them where it is, and offer to raise a ticket if "
        "the delivery date has passed. Escalate to a human on any refund."
    )

    def test_a_chat_bot_needs_no_call_type(self):
        # The model must not be made to invent a direction for a bot with no
        # phone: that invention is how every bot ends up inbound.
        payload = bot_from_brief.resolve(
            {"name": "Order help", "channel": "chat", "spec": self.SPEC}
        )
        assert payload["args"]["channel"] == "chat"

    def test_a_voice_bot_still_has_to_say_who_rings_whom(self):
        with pytest.raises(bot_from_brief.BriefError):
            bot_from_brief.resolve({"name": "Reception", "spec": self.SPEC})

    def test_no_channel_means_voice_which_still_needs_a_direction(self):
        payload = bot_from_brief.resolve(
            {"name": "Reception", "call_type": "inbound", "spec": self.SPEC}
        )
        assert payload["args"]["channel"] == "voice"
        assert payload["args"]["call_type"] == "inbound"

    def test_a_channel_nobody_offers_is_refused_rather_than_guessed(self):
        with pytest.raises(bot_from_brief.BriefError):
            bot_from_brief.resolve(
                {"name": "Order help", "channel": "telepathy", "spec": self.SPEC}
            )

    def test_the_tool_no_longer_requires_a_call_type(self):
        required = bot_from_brief.tool_schema()["parameters"]["required"]
        assert "call_type" not in required
        assert "spec" in required

    def test_the_tool_offers_the_channel_the_runtime_actually_has(self):
        properties = bot_from_brief.tool_schema()["parameters"]["properties"]
        assert properties["channel"]["enum"] == ["voice", "chat"]


class TestTheChannelSurvivesASave:
    """The failure this guards is silent in every direction.

    A settings save sends a sparse block and replaces the stored one, and an
    absent channel reads as voice by design. So a screen that saves without
    knowing about the field turns a chat bot back into a call bot, raises
    nothing, logs nothing, and shows nothing -- until a chat window is
    thanked for calling.
    """

    def test_a_save_that_never_mentions_the_channel_keeps_it(self):
        saved = preserve_channel({"end_call_phrases": ["bye"]}, {"channel": "chat"})
        assert saved["channel"] == "chat"
        assert saved["end_call_phrases"] == ["bye"]

    def test_a_save_that_sets_the_channel_is_obeyed(self):
        # Deliberately putting a chat bot on the phone is allowed. This only
        # protects against never being asked.
        saved = preserve_channel({"channel": "voice"}, {"channel": "chat"})
        assert saved["channel"] == "voice"

    def test_a_voice_bot_gains_nothing(self):
        assert preserve_channel({"end_call_phrases": []}, {}) == {
            "end_call_phrases": []
        }

    def test_a_bot_with_no_stored_configuration_is_not_a_crash(self):
        assert preserve_channel({"a": 1}, None) == {"a": 1}

    def test_a_request_that_sends_no_configuration_block_is_left_alone(self):
        # None means "this save is not about configuration". Returning a dict
        # here would write an empty block over the stored one.
        assert preserve_channel(None, {"channel": "chat"}) is None

    def test_the_original_block_is_not_mutated(self):
        incoming = {"end_call_phrases": []}
        preserve_channel(incoming, {"channel": "chat"})
        assert "channel" not in incoming


def _bot(configurations=None, *, is_live=True):
    from types import SimpleNamespace

    from api.enums import WorkflowStatus

    return SimpleNamespace(
        id=7,
        is_live=is_live,
        status=WorkflowStatus.ACTIVE.value,
        workflow_configurations=configurations,
    )


class TestAChatBotIsNotHandedCalls:
    """One gate, because all four call paths ask this module.

    A chat bot has no voice configuration and a graph written for somebody
    reading. Handed a call it would answer in a default voice with prompts
    about messages -- which is not a crash, so nothing would catch it.
    """

    def test_it_refuses(self):
        with pytest.raises(liveness.AgentNotTakingCalls) as caught:
            liveness.assert_workflow_may_take_calls(_bot({"channel": "chat"}))
        assert caught.value.reason == "agent_is_chat_only"

    def test_the_refusal_does_not_send_anybody_to_look_for_a_toggle(self):
        # "Paused" would be a lie that costs somebody ten minutes on the
        # agent list, where the real answer does not appear.
        with pytest.raises(liveness.AgentNotTakingCalls) as caught:
            liveness.assert_workflow_may_take_calls(_bot({"channel": "chat"}))
        assert "paused" not in str(caught.value).lower()
        assert "writing" in str(caught.value).lower()

    def test_it_refuses_even_when_switched_on(self):
        # The switch is about willingness. This is about ability.
        with pytest.raises(liveness.AgentNotTakingCalls):
            liveness.assert_workflow_may_take_calls(
                _bot({"channel": "chat"}, is_live=True)
            )

    def test_a_voice_bot_is_unaffected(self):
        liveness.assert_workflow_may_take_calls(_bot({"channel": "voice"}))
        liveness.assert_workflow_may_take_calls(_bot({}))
        liveness.assert_workflow_may_take_calls(_bot(None))

    def test_workflow_is_live_says_no_for_a_chat_bot(self):
        # The four dispatchers read this as well as the assert; a flag
        # honoured by one and not the other is the bug this module exists
        # to prevent.
        assert liveness.workflow_is_live(_bot({"channel": "chat"})) is False
        assert liveness.workflow_is_live(_bot({"channel": "voice"})) is True

    def test_a_workflow_object_with_no_configurations_attribute_is_not_a_crash(self):
        from types import SimpleNamespace

        from api.enums import WorkflowStatus

        bare = SimpleNamespace(id=7, is_live=True, status=WorkflowStatus.ACTIVE.value)
        liveness.assert_workflow_may_take_calls(bare)
        assert liveness.workflow_is_live(bare) is True


class TestAChatBotIsGivenNoVoice:
    """The wizard's preset writes a whole managed stack onto the agent.

    For a chat bot most of that stack describes equipment it does not have.
    Writing it anyway is harmless at runtime and a lie on the settings
    screen, which would then offer to change the voice of a thing that
    never speaks.
    """

    def test_a_voice_bot_gets_its_speech_slots(self):
        from api.services.configuration.agent_options import managed_stack_override

        override = managed_stack_override(
            voice="anushka",
            llm_tier="standard",
            stt_tier="standard",
            tts_tier="standard",
        )
        assert override

    def test_a_chat_bot_keeps_a_brain_and_loses_the_rest(self):
        from api.services.configuration.agent_options import managed_stack_override

        override = managed_stack_override(
            voice="",
            llm_tier="standard",
            stt_tier="",
            tts_tier="",
            realtime_tier="",
        )
        # Still an override -- a chat bot has a model. It just has no mouth.
        rendered = str(override)
        assert "anushka" not in rendered
