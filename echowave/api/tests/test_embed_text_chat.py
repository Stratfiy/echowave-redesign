"""The public text surface for the website widget.

The existing text-chat routes serve the editor's test chat: they need a
logged-in user and run the draft. A visitor on a customer's website is neither,
so this is a separate surface with embed-token authorisation — and that
difference is where the mistakes live. Anyone on the internet can call these.
"""

import pytest

from api.routes import public_embed


class TestVisibleMessages:
    """What the browser is allowed to see.

    A projection, not a filter. The stored session is a list of turns, each
    carrying node transitions, tool events, token usage, cost and a checkpoint
    of the graph alongside the two lines of conversation — and this must hand
    back only the two.

    These fixtures are the shape the runner actually writes (see
    ``text_chat_session_service``). An earlier version of this test invented a
    flat ``messages`` list, which passed while the real endpoint returned an
    empty transcript for every message a visitor sent.
    """

    def test_a_turn_yields_the_question_then_the_answer(self):
        data = {
            "turns": [
                {
                    "id": "turn_1",
                    "status": "completed",
                    "user_message": {"text": "do you do root canal"},
                    "assistant_message": {"text": "Yes, we do."},
                }
            ]
        }
        assert public_embed._visible_messages(data) == [
            {"role": "user", "content": "do you do root canal"},
            {"role": "assistant", "content": "Yes, we do."},
        ]

    def test_a_turn_with_only_one_side_still_comes_through(self):
        """The opening greeting has no question, and a pending turn has no
        reply. Dropping either would lose the visitor's own message from the
        screen while the agent was still thinking."""
        data = {
            "turns": [
                {"assistant_message": {"text": "Hello, Sharma Dental."}},
                {"user_message": {"text": "are you open sunday"}},
            ]
        }
        assert public_embed._visible_messages(data) == [
            {"role": "assistant", "content": "Hello, Sharma Dental."},
            {"role": "user", "content": "are you open sunday"},
        ]

    def test_node_transitions_and_tool_events_never_reach_the_visitor(self):
        """Node names are the agent's structure. Returning them hands a
        competitor the design and a caller the guardrails to talk around."""
        data = {
            "turns": [
                {
                    "user_message": {"text": "book me in"},
                    "assistant_message": {"text": "One moment."},
                    "events": [
                        {
                            "type": "node_transition",
                            "payload": {"node_id": "agent-3", "node_name": "Booking"},
                        }
                    ],
                }
            ]
        }
        assert public_embed._visible_messages(data) == [
            {"role": "user", "content": "book me in"},
            {"role": "assistant", "content": "One moment."},
        ]

    def test_usage_cost_and_checkpoints_are_not_forwarded(self):
        """A turn carries what we paid for it and the whole graph state. None
        of that is the visitor's, and some of it is ours alone."""
        data = {
            "turns": [
                {
                    "assistant_message": {"text": "Hello."},
                    "usage": {
                        "llm": {"cost_inr": 0.42},
                        "key_sources": {"llm": "managed"},
                    },
                    "checkpoint_after_turn": {"anchor": "agent-1", "variables": {}},
                }
            ]
        }
        assert public_embed._visible_messages(data) == [
            {"role": "assistant", "content": "Hello."}
        ]

    def test_a_message_carrying_extra_keys_is_reduced_to_its_text(self):
        data = {
            "turns": [
                {
                    "assistant_message": {
                        "text": "Hello.",
                        "created_at": "2026-09-03T04:57:19Z",
                        "node_id": "agent-1",
                    }
                }
            ]
        }
        assert public_embed._visible_messages(data) == [
            {"role": "assistant", "content": "Hello."}
        ]

    def test_empty_and_non_string_text_is_skipped(self):
        """An empty one would render as a blank bubble; a structured block as
        an object the widget cannot draw."""
        data = {
            "turns": [
                {"assistant_message": {"text": ""}},
                {"assistant_message": {"text": None}},
                {"assistant_message": {"text": [{"type": "text", "text": "hi"}]}},
                {"assistant_message": None},
                {"user_message": {"text": "ok"}},
            ]
        }
        assert public_embed._visible_messages(data) == [
            {"role": "user", "content": "ok"}
        ]

    @pytest.mark.parametrize(
        "data",
        [None, {}, {"turns": None}, {"turns": []}, {"turns": ["not a turn"]}],
    )
    def test_an_empty_or_malformed_session_is_not_a_crash(self, data):
        assert public_embed._visible_messages(data) == []


class TestTheRequestShape:
    def test_a_message_is_length_capped(self):
        """Unauthenticated and public. Without a cap, one POST can push an
        arbitrary amount of text into an LLM call the customer pays for."""
        field = public_embed.EmbedTextMessageRequest.model_fields["text"]
        limits = [m for m in field.metadata if hasattr(m, "max_length")]
        assert limits and limits[0].max_length <= 2000

    def test_an_empty_message_is_rejected(self):
        with pytest.raises(Exception):
            public_embed.EmbedTextMessageRequest(text="")


class TestModeSelection:
    def test_voice_is_the_default(self):
        """Every widget already deployed sends no mode. It must keep getting a
        voice session."""
        assert public_embed.InitEmbedRequest(token="t").mode == "voice"

    def test_text_mode_is_accepted(self):
        assert public_embed.InitEmbedRequest(token="t", mode="text").mode == "text"


class TestPreflight:
    """The widget runs on the customer's domain, so every request is
    cross-origin and dies at the preflight if this is wrong."""

    @pytest.mark.asyncio
    async def test_the_text_path_is_routed_to_its_own_check(self, monkeypatch):
        called = {}

        async def fake(session_token, origin):
            called["token"] = session_token
            from fastapi import Response

            return Response(status_code=204)

        monkeypatch.setattr(public_embed, "_text_message_preflight_response", fake)
        res = await public_embed.build_public_embed_preflight_response(
            "/api/v1/public/embed/text/abc123/messages",
            "https://clinic.example",
            "POST",
        )
        assert res is not None
        assert called["token"] == "abc123"

    @pytest.mark.asyncio
    async def test_a_non_post_preflight_is_refused(self):
        res = await public_embed.build_public_embed_preflight_response(
            "/api/v1/public/embed/text/abc123/messages", "https://clinic.example", "GET"
        )
        assert res.status_code == 405

    @pytest.mark.asyncio
    async def test_an_unrelated_path_is_left_alone(self):
        """Returning a response here would answer for routes this middleware
        does not own."""
        res = await public_embed.build_public_embed_preflight_response(
            "/api/v1/workflow/1", "https://clinic.example", "POST"
        )
        assert res is None
