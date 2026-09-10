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

    The session stores the conversation as turns, each with the visitor's
    user_message and the agent's assistant_message, alongside events, usage,
    node transitions and the checkpoint. A whitelist rather than a filter:
    exactly two text fields are copied out and nothing else on a turn is ever
    forwarded, because a blacklist is one new key away from publishing how
    the agent works to anyone who opens the network tab.
    """

    @staticmethod
    def _turn(user=None, assistant=None, **extra):
        turn = {
            "id": "turn_x",
            "status": "completed",
            "created_at": "2026-09-10T16:30:11+00:00",
            "user_message": {"text": user, "created_at": "t"}
            if user is not None
            else None,
            "assistant_message": (
                {"text": assistant, "created_at": "t"}
                if assistant is not None
                else None
            ),
            "events": [],
            "usage": {},
        }
        turn.update(extra)
        return turn

    def test_user_and_assistant_turns_come_through_in_order(self):
        data = {
            "turns": [self._turn(user="do you do root canal", assistant="Yes, we do.")]
        }
        assert public_embed._visible_messages(data) == [
            {"role": "user", "content": "do you do root canal"},
            {"role": "assistant", "content": "Yes, we do."},
        ]

    def test_the_opening_greeting_has_no_user_side_and_still_shows(self):
        """The agent greets first: the opening turn has user_message None and
        only an assistant line. That line must reach the visitor, or the chat
        opens on a blank bubble waiting for someone to speak."""
        data = {
            "turns": [
                self._turn(assistant="Hello, welcome to Elock support."),
                self._turn(user="Hi", assistant="How can I help?"),
            ]
        }
        assert public_embed._visible_messages(data) == [
            {"role": "assistant", "content": "Hello, welcome to Elock support."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "How can I help?"},
        ]

    def test_a_pending_turn_shows_the_visitor_line_only(self):
        """While the agent is still answering, the visitor's own message is
        already on screen and nothing is invented for the reply."""
        data = {"turns": [self._turn(user="my lock is stuck", status="pending")]}
        assert public_embed._visible_messages(data) == [
            {"role": "user", "content": "my lock is stuck"}
        ]

    def test_events_usage_and_node_names_never_reach_the_visitor(self):
        """Events carry node transitions and tool activity; usage carries cost
        and token counts; the checkpoint carries the agent's internal state.
        Returning any of it hands a competitor the flow and a caller the
        guardrails to talk around."""
        data = {
            "turns": [
                self._turn(
                    assistant="One moment.",
                    events=[
                        {
                            "type": "node_transition",
                            "payload": {"node_name": "Escalate"},
                        },
                        {
                            "type": "tool_call",
                            "payload": {"name": "lookup", "args": {}},
                        },
                    ],
                    usage={"llm": {"tokens": 512}, "cost_inr": 0.42},
                    checkpoint_after_turn={"anchor_turn_id": "turn_x"},
                )
            ]
        }
        assert public_embed._visible_messages(data) == [
            {"role": "assistant", "content": "One moment."}
        ]

    def test_only_role_and_content_are_copied(self):
        """A message object carries created_at; a turn carries ids and status.
        None of it is the visitor's."""
        data = {"turns": [self._turn(user="ok", assistant="Sure.")]}
        for message in public_embed._visible_messages(data):
            assert set(message) == {"role", "content"}

    def test_non_string_and_empty_text_is_skipped(self):
        """A structured text block would reach the browser as an object the
        widget cannot render, and an empty one as a blank bubble."""
        data = {
            "turns": [
                {**self._turn(), "assistant_message": {"text": [{"type": "text"}]}},
                {**self._turn(), "assistant_message": {"text": ""}},
                {**self._turn(), "assistant_message": {"text": None}},
                {**self._turn(), "assistant_message": "not a dict"},
                self._turn(user="ok"),
            ]
        }
        assert public_embed._visible_messages(data) == [
            {"role": "user", "content": "ok"}
        ]

    @pytest.mark.parametrize(
        "data", [None, {}, {"turns": None}, {"turns": []}, {"turns": ["junk", 3]}]
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


class TestTextMessageIsTenantScoped:
    """The session loader takes a keyword-only organization_id. The embed
    route once called it with the run id alone, which raised a TypeError
    before the route's own error handling — a bare 500 with an empty body
    on every message a visitor sent. The token is the only proof of which
    tenant the visitor may talk to, so it is the org the lookup is scoped to.
    """

    @pytest.mark.asyncio
    async def test_the_session_lookup_is_scoped_to_the_tokens_organization(
        self, monkeypatch
    ):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        from fastapi import Response

        embed_session = SimpleNamespace(workflow_run_id=42, embed_token_id=7)
        embed_token = SimpleNamespace(
            id=7, workflow_id=17, organization_id=99, allowed_domains=[]
        )

        async def resolve(session_token, request, response):
            return embed_session, embed_token

        monkeypatch.setattr(public_embed, "_resolve_embed_session", resolve)

        loader = AsyncMock(return_value=None)  # 404 path: enough to prove the call
        monkeypatch.setattr(
            public_embed.db_client, "get_workflow_run_text_session", loader
        )

        request = MagicMock()
        with pytest.raises(public_embed.HTTPException) as excinfo:
            await public_embed.post_embed_text_message(
                "emb_session_x",
                public_embed.EmbedTextMessageRequest(text="Hi"),
                request,
                Response(),
            )
        assert excinfo.value.status_code == 404  # reached the handler, no TypeError

        loader.assert_awaited_once_with(42, organization_id=99)
