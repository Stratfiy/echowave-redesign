"""A send card in the thread must show who it goes to and exactly what it says.

Found building an outreach agent end to end (October 2026): Decibyl proposed
one Gmail send per lead, and each card in Chat read "Gmail — Send Email via
gmail", the model's one-line reason, and "It cannot be undone". The
recipient, the subject and the body were in the card's arguments and on no
screen in the thread. A business owner pressing Confirm on ten of those is
approving ten emails nobody has read.

The fix is the card's own ``preview``, which ActionCard already renders
while the card is waiting: built from the arguments, never from the model's
prose, untruncated, and rebuilt when the card is edited.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.enums import ToolCategory
from api.services.workflow import actions

BODY = "Hi Asha,\n\nI saw Lotus Dental opened a second clinic in Pune.\n\nRavi"


def _tool(slug: str, *, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        tool_uuid=f"uuid-{slug.lower()}",
        name=name,
        category=ToolCategory.COMPOSIO.value,
        definition={
            "type": "composio",
            "config": {"tool_slug": slug, "toolkit": slug.split("_")[0].lower()},
            "connected": True,
        },
    )


async def _card(tool: SimpleNamespace, arguments: dict) -> dict:
    with (
        patch.object(
            actions.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
        ),
        patch("api.services.workflow.connected_tools.is_connected", return_value=True),
    ):
        return await actions.resolve(
            organization_id=1,
            workflow_id=None,
            arguments={
                "action": actions.RUN_TOOL,
                "why": "Asked in the thread",
                "tool_uuid": tool.tool_uuid,
                "arguments": arguments,
            },
        )


class TestTheCardShowsTheMail:
    async def test_recipient_subject_and_body_are_on_the_card(self):
        card = await _card(
            _tool("GMAIL_SEND_EMAIL", name="Gmail — Send Email"),
            {
                "recipient_email": "asha@lotusdental.example.com",
                "subject": "Your second clinic",
                "body": BODY,
            },
        )
        preview = card.get("preview") or ""
        assert "To: asha@lotusdental.example.com" in preview
        assert "Subject: Your second clinic" in preview
        # The whole body, as it will be sent: not a summary, not clipped.
        assert BODY in preview

    async def test_a_long_body_is_not_clipped(self):
        body = "word " * 2_000
        card = await _card(
            _tool("GMAIL_SEND_EMAIL", name="Gmail — Send Email"),
            {"recipient_email": "a@example.com", "subject": "s", "body": body},
        )
        assert body.strip() in card["preview"]

    async def test_a_list_of_recipients_is_named_in_full(self):
        card = await _card(
            _tool("OUTLOOK_SEND_EMAIL", name="Outlook — Send Email"),
            {
                "to_recipients": ["a@example.com", "b@example.org"],
                "subject": "Hello",
                "body": "Short note",
            },
        )
        assert "a@example.com" in card["preview"]
        assert "b@example.org" in card["preview"]

    async def test_a_tool_with_nothing_to_show_has_no_preview(self):
        card = await _card(_tool("GMAIL_FETCH_EMAILS", name="Gmail — Fetch"), {})
        assert not card.get("preview")

    async def test_the_preview_is_not_part_of_what_is_approved(self):
        """The version binds the arguments; the preview is drawn from them,
        so it cannot say something the arguments do not."""
        card = await _card(
            _tool("GMAIL_SEND_EMAIL", name="Gmail — Send Email"),
            {"recipient_email": "a@example.com", "subject": "s", "body": "b"},
        )
        same = dict(card)
        same["preview"] = "To: someone-else@example.com"
        assert actions.payload_version(card) == actions.payload_version(same)


class TestAnEditRedrawsIt:
    async def test_revise_rebuilds_the_preview(self, monkeypatch):
        tool = _tool("GMAIL_SEND_EMAIL", name="Gmail — Send Email")
        card = await _card(
            tool,
            {"recipient_email": "a@example.com", "subject": "Old", "body": "Old"},
        )
        card["version"] = actions.payload_version(card)
        event = SimpleNamespace(id=7, organization_id=1, payload=card)
        monkeypatch.setattr(actions, "_ledger_on", lambda _org: True)
        monkeypatch.setattr(actions, "_proposal", AsyncMock(return_value=event))
        monkeypatch.setattr(actions, "_move", AsyncMock(return_value=None))
        monkeypatch.setattr(actions.audit_log, "record", AsyncMock())
        updated = await actions.revise(
            organization_id=1,
            event_id=7,
            arguments={
                "recipient_email": "a@example.com",
                "subject": "New subject",
                "body": "New body",
            },
            user_id=3,
        )
        assert "Subject: New subject" in updated["preview"]
        assert "New body" in updated["preview"]
        assert "Old" not in updated["preview"]
