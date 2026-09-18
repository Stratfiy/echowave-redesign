"""A share link is the demo a customer texts a prospect. Its docstring says
every call on it is paid from the owner's credits, and for voice that is
true: the call is authorised where it is answered. Text had no such point.
Seen live: an account at zero credit, refused in its own chat, answered a
stranger on its share link for free, and would have gone on doing so.

So a text session on the link is authorised like any run, each reply is
charged like a reply in a channel, and a session that began funded stops
at the floor. The visitor is told only that the assistant is not taking
messages: the owner's balance is the owner's business.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from api.routes import public_embed
from api.services.billing import events as billing_events


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/embed/init",
            "headers": [(b"origin", b"https://app.decibyl.ai")],
            "client": ("203.0.113.9", 4321),
            "query_string": b"",
        }
    )


def _token():
    return SimpleNamespace(
        id=11,
        token="emb_x",
        workflow_id=3,
        organization_id=7,
        created_by=1,
        is_active=True,
        expires_at=None,
        usage_limit=None,
        usage_count=0,
        allowed_domains=[],
        settings={},
        daily_minutes_cap=None,
    )


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
class TestStartingAChat:
    async def _init(self, *, quota):
        with (
            patch.object(
                public_embed.db_client,
                "get_embed_token_by_token",
                AsyncMock(return_value=_token()),
            ),
            patch.object(public_embed, "validate_origin", lambda *a: True),
            patch.object(
                public_embed.db_client,
                "create_workflow_run",
                AsyncMock(return_value=SimpleNamespace(id=41)),
            ),
            patch.object(
                public_embed,
                "authorize_workflow_run_start",
                AsyncMock(return_value=quota),
            ),
            patch.object(
                public_embed.db_client, "create_embed_session", AsyncMock()
            ) as session,
            patch.object(
                public_embed.db_client, "increment_embed_token_usage", AsyncMock()
            ),
            patch.object(public_embed, "_start_text_session", AsyncMock()),
            patch.object(public_embed.one_shot_run, "close", AsyncMock()) as close,
        ):
            try:
                result = await public_embed.initialize_embed_session(
                    _request(),
                    public_embed.InitEmbedRequest(token="emb_x", mode="text"),
                    Response(),
                )
            except HTTPException as exc:
                return exc, session, close
            return result, session, close

    async def test_an_unfunded_owner_turns_the_visitor_away_without_saying_why(self):
        refused = SimpleNamespace(
            has_quota=False,
            error_code="insufficient_credit",
            error_message="Calling stops when your balance falls below ₹20.",
        )
        exc, session, close = await self._init(quota=refused)
        assert isinstance(exc, HTTPException) and exc.status_code == 403
        assert exc.detail == public_embed.NOT_TAKING_MESSAGES
        assert "₹" not in exc.detail and "credit" not in exc.detail.lower()
        session.assert_not_awaited()
        close.assert_awaited_once_with(41)

    async def test_a_funded_owner_gets_a_session(self):
        result, session, _ = await self._init(quota=SimpleNamespace(has_quota=True))
        assert result.workflow_run_id == 41
        session.assert_awaited_once()


def _text_session(turn_id="t9"):
    return SimpleNamespace(
        session_data={
            "turns": [
                {
                    "id": turn_id,
                    "status": "completed",
                    "user_message": {"text": "open on Saturday?"},
                    "assistant_message": {"text": "Yes, 9 to 6."},
                    "events": [],
                    "usage": {},
                }
            ]
        },
        workflow_run=SimpleNamespace(is_completed=False),
    )


@pytest.mark.asyncio
class TestEachReply:
    async def _send(self, *, funded: bool):
        session = _text_session()
        with (
            patch.object(
                public_embed,
                "_resolve_embed_session",
                AsyncMock(return_value=(SimpleNamespace(workflow_run_id=41), _token())),
            ),
            patch.object(
                public_embed.db_client,
                "get_workflow_run_text_session",
                AsyncMock(return_value=session),
            ),
            patch.object(public_embed.db_client, "async_session", lambda: _Session()),
            patch.object(
                public_embed.reservations, "has_credit", AsyncMock(return_value=funded)
            ),
            patch(
                "api.services.workflow.text_chat_session_service.append_text_chat_user_message",
                AsyncMock(return_value=session),
            ),
            patch(
                "api.services.workflow.text_chat_session_service.execute_pending_text_chat_turn",
                AsyncMock(return_value=session),
            ) as turn,
            patch.object(
                public_embed.billing_events,
                "charge_in_own_session",
                AsyncMock(return_value=100),
            ) as charge,
        ):
            try:
                result = await public_embed.post_embed_text_message(
                    "sess_x",
                    public_embed.EmbedTextMessageRequest(text="open on Saturday?"),
                    _request(),
                    Response(),
                )
            except HTTPException as exc:
                return exc, turn, charge
            return result, turn, charge

    async def test_is_charged_like_a_reply_in_a_channel(self):
        result, turn, charge = await self._send(funded=True)
        assert [m["content"] for m in result.messages] == [
            "open on Saturday?",
            "Yes, 9 to 6.",
        ]
        turn.assert_awaited_once()
        charge.assert_awaited_once()
        kwargs = charge.await_args.kwargs
        assert kwargs["organization_id"] == 7
        assert kwargs["event"] == billing_events.TEXT_REPLY
        assert kwargs["ref_id"] == "41:t9"

    async def test_stops_at_the_floor_before_the_model_runs(self):
        exc, turn, charge = await self._send(funded=False)
        assert isinstance(exc, HTTPException) and exc.status_code == 403
        assert exc.detail == public_embed.NOT_TAKING_MESSAGES
        turn.assert_not_awaited()
        charge.assert_not_awaited()
