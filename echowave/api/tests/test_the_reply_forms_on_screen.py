"""The reply is shown forming before it is a row.

Arrival tests: the streaming client yields text as it grows and tool calls
whole at the end, for both vendors that stream; Gemini falls back to one
request; the draft is written as Decibyl speaks and cleared after the row;
the draft route returns it for the thread that asked.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from api.services.agent_builder import client
from api.services.workflow import reply_draft


def _sse(events: list[dict], done: bool = False) -> str:
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
    return body + ("data: [DONE]\n\n" if done else "")


def _transport(body: str, status: int = 200):
    async def handler(request: httpx.Request) -> httpx.Response:
        handler.payload = json.loads(request.content)  # type: ignore[attr-defined]
        return httpx.Response(status, text=body)

    return httpx.MockTransport(handler), handler


@pytest.mark.asyncio
class TestStreaming:
    async def test_anthropic_text_grows_and_a_tool_call_arrives_whole(self):
        events = [
            {"type": "message_start"},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Front desk "},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "took 8 calls."},
            },
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "propose_action",
                },
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"action": "turn_',
                },
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": 'bot_on", "why": "x"}',
                },
            },
            {"type": "message_stop"},
        ]
        transport, handler = _transport(_sse(events))
        seen: list[str] = []

        async def on_text(text: str) -> None:
            seen.append(text)

        real = httpx.AsyncClient

        def fake_client(**kwargs):
            return real(transport=transport, **kwargs)

        with patch(
            "api.services.agent_builder.client.httpx.AsyncClient",
            side_effect=fake_client,
        ):
            reply = await client.stream(
                provider="anthropic",
                model="m",
                api_key="k",
                system="s",
                conversation=client.Conversation(),
                on_text=on_text,
                tools=[
                    {
                        "name": "propose_action",
                        "description": "d",
                        "parameters": {"type": "object"},
                    }
                ],
            )
        assert handler.payload["stream"] is True
        assert reply.text == "Front desk took 8 calls."
        assert seen and seen[-1].startswith("Front desk")
        assert reply.tool_calls[0].name == "propose_action"
        assert reply.tool_calls[0].arguments == {"action": "turn_bot_on", "why": "x"}

    async def test_openai_fragments_assemble(self):
        events = [
            {"choices": [{"delta": {"role": "assistant", "content": ""}}]},
            {"choices": [{"delta": {"content": "Namaste"}}]},
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c1",
                                    "function": {
                                        "name": "propose_action",
                                        "arguments": '{"ac',
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {
                                        "arguments": 'tion": "turn_bot_off", "why": "y"}'
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        transport, _ = _transport(_sse(events, done=True))
        real = httpx.AsyncClient
        with patch(
            "api.services.agent_builder.client.httpx.AsyncClient",
            side_effect=lambda **kw: real(transport=transport, **kw),
        ):
            reply = await client.stream(
                provider="openai",
                model="m",
                api_key="k",
                system="s",
                conversation=client.Conversation(),
                on_text=AsyncMock(),
            )
        assert reply.text == "Namaste"
        assert reply.tool_calls[0].arguments == {"action": "turn_bot_off", "why": "y"}

    async def test_gemini_falls_back_to_one_request(self):
        with patch(
            "api.services.agent_builder.client.complete",
            new=AsyncMock(return_value=client.ModelReply(text="whole")),
        ) as complete:
            reply = await client.stream(
                provider="google",
                model="m",
                api_key="k",
                system="s",
                conversation=client.Conversation(),
                on_text=AsyncMock(),
            )
        assert reply.text == "whole"
        complete.assert_awaited_once()

    async def test_a_rejected_key_is_the_same_message_as_without_streaming(self):
        transport, _ = _transport('{"error": "no"}', status=401)
        real = httpx.AsyncClient
        with patch(
            "api.services.agent_builder.client.httpx.AsyncClient",
            side_effect=lambda **kw: real(transport=transport, **kw),
        ):
            with pytest.raises(
                client.BuilderClientError, match="provider key was rejected"
            ):
                await client.stream(
                    provider="anthropic",
                    model="m",
                    api_key="k",
                    system="s",
                    conversation=client.Conversation(),
                    on_text=AsyncMock(),
                )


class TestTheDraftKey:
    def test_one_key_per_thread(self):
        assert reply_draft.key(7) == "reply_draft:7:assistant"
        assert reply_draft.key(7, 3) == "reply_draft:7:bot:3"


@pytest.mark.asyncio
class TestDecibylSpeaks:
    async def test_the_draft_is_written_as_it_forms_and_cleared_after_the_row(self):
        from api.services.workflow import decibyl

        async def fake_stream(**kwargs):
            await kwargs["on_text"]("Front desk")
            return client.ModelReply(text="Front desk took 8 calls.")

        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with (
            patch(
                "api.services.workflow.decibyl.build_context",
                new=AsyncMock(return_value=""),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.agent_events",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.async_session",
                return_value=session,
            ),
            patch(
                "api.services.agent_builder.settings.resolve_model",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        provider="anthropic", model="m", api_key="k"
                    )
                ),
            ),
            patch(
                "api.services.agent_builder.client.stream",
                new=AsyncMock(side_effect=fake_stream),
            ),
            patch(
                "api.services.workflow.decibyl.reply_draft.set_draft", new=AsyncMock()
            ) as set_draft,
            patch(
                "api.services.workflow.decibyl.reply_draft.clear", new=AsyncMock()
            ) as clear,
            patch(
                "api.services.workflow.decibyl.agent_timeline.record", new=AsyncMock()
            ) as record,
        ):
            body = await decibyl.answer(7, "what happened?")
        assert body == "Front desk took 8 calls."
        assert set_draft.await_args.args == (7, "Front desk")
        clear.assert_awaited_once_with(7)
        record.assert_awaited_once()

    async def test_the_route_returns_the_thread_that_asked(self):
        from httpx import ASGITransport, AsyncClient

        from api.app import app
        from api.services.auth.depends import get_user

        app.dependency_overrides[get_user] = lambda: SimpleNamespace(
            id=42, selected_organization_id=7
        )
        try:
            with patch(
                "api.routes.agent_timeline.reply_draft.get",
                new=AsyncMock(return_value="Front desk"),
            ) as get:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as http:
                    response = await http.get("/api/v1/timeline/draft?workflow_id=3")
        finally:
            app.dependency_overrides.pop(get_user, None)
        assert response.status_code == 200
        assert response.json() == {"text": "Front desk"}
        assert get.await_args.kwargs == {"workflow_id": 3}
        assert get.await_args.args == (7,)
