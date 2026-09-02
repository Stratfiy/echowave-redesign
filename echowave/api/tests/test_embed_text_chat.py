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

    A whitelist rather than a filter. The session carries tool calls, node
    names and the agent's own reasoning, and a blacklist is one new key away
    from publishing how the agent works to anyone who opens the network tab.
    """

    def test_user_and_assistant_turns_come_through(self):
        data = {
            "messages": [
                {"role": "user", "content": "do you do root canal"},
                {"role": "assistant", "content": "Yes, we do."},
            ]
        }
        assert public_embed._visible_messages(data) == [
            {"role": "user", "content": "do you do root canal"},
            {"role": "assistant", "content": "Yes, we do."},
        ]

    def test_system_prompts_never_reach_the_visitor(self):
        """The system message is the agent's instructions. Returning it hands
        a competitor the prompt and a caller the guardrails to talk around."""
        data = {
            "messages": [
                {"role": "system", "content": "You are a clinic receptionist..."},
                {"role": "assistant", "content": "Hello."},
            ]
        }
        assert public_embed._visible_messages(data) == [
            {"role": "assistant", "content": "Hello."}
        ]

    def test_tool_calls_and_unknown_roles_are_dropped(self):
        data = {
            "messages": [
                {"role": "tool", "content": "{'slots': [...]}"},
                {"role": "developer", "content": "internal"},
                {"role": "assistant", "content": "One moment."},
            ]
        }
        assert public_embed._visible_messages(data) == [
            {"role": "assistant", "content": "One moment."}
        ]

    def test_extra_keys_on_a_message_are_not_forwarded(self):
        """Only role and content are copied. A message may also carry node ids,
        latency, token counts and cost — none of which is the visitor's."""
        data = {
            "messages": [
                {
                    "role": "assistant",
                    "content": "Hello.",
                    "node_id": "agent-1",
                    "cost_inr": 0.42,
                }
            ]
        }
        assert public_embed._visible_messages(data) == [
            {"role": "assistant", "content": "Hello."}
        ]

    def test_non_string_and_empty_content_is_skipped(self):
        """A structured content block would reach the browser as an object the
        widget cannot render, and an empty one as a blank bubble."""
        data = {
            "messages": [
                {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
                {"role": "assistant", "content": ""},
                {"role": "assistant", "content": None},
                {"role": "user", "content": "ok"},
            ]
        }
        assert public_embed._visible_messages(data) == [
            {"role": "user", "content": "ok"}
        ]

    @pytest.mark.parametrize("data", [None, {}, {"messages": None}, {"messages": []}])
    def test_an_empty_session_is_not_a_crash(self, data):
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
            "/api/v1/public/embed/text/abc123/messages", "https://clinic.example", "POST"
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
