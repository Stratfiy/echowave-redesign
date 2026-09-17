"""A card must state its own effect where the person decides.

The card knows whether its action can be undone -- every ``run_tool`` card
carries ``reversible: False`` -- and said so nowhere. ``reversible`` was read
only after the thing had run, to decide whether to offer "Put it back". At
the Confirm button the operator saw two things: a label built from the tool's
name, and ``why``, which the model writes in its own words.

So the model's words were the only account of what pressing Confirm would do,
and they were wrong. Asked for a draft and nothing sent, Decibyl proposed
GMAIL_REPLY_TO_THREAD -- a send -- and wrote: "Confirm it on the card and
it'll sit as a draft, nothing sends automatically." The card's own payload
said ``reversible: False`` in the same breath. Confirming would have emailed
the sender.

The fix is not to trust the model less in prose. It is to put the fact on the
card: one line, derived from what the tool is, that the model does not write
and cannot contradict without the contradiction being visible.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.enums import ToolCategory
from api.services.workflow import actions


def _tool(slug: str, *, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        tool_uuid=f"uuid-{slug.lower()}",
        name=name,
        category=ToolCategory.COMPOSIO.value,
        definition={
            "type": "composio",
            "config": {"tool_slug": slug, "toolkit": slug.split("_")[0].lower()},
        },
    )


async def _card(tool: SimpleNamespace) -> dict:
    with patch.object(
        actions.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
    ):
        return await actions.resolve(
            organization_id=1,
            workflow_id=None,
            arguments={
                "action": actions.RUN_TOOL,
                "why": "Asked in the thread",
                "tool_uuid": tool.tool_uuid,
                "arguments": {},
            },
        )


class TestTheCardCarriesItsEffect:
    async def test_a_send_says_it_cannot_be_undone(self):
        card = await _card(_tool("GMAIL_SEND_EMAIL", name="Gmail — Send Email"))
        assert card["reversible"] is False
        assert "undone" in card["effect"].lower()

    async def test_a_reply_is_a_send(self):
        """The exact tool that was described as a draft."""
        card = await _card(_tool("GMAIL_REPLY_TO_THREAD", name="Gmail — Reply"))
        assert "undone" in card["effect"].lower()
        assert "draft" not in card["effect"].lower()

    async def test_a_draft_says_nothing_is_sent(self):
        card = await _card(
            _tool("GMAIL_CREATE_EMAIL_DRAFT", name="Gmail — Create Draft")
        )
        assert "draft" in card["effect"].lower()
        assert "nothing is sent" in card["effect"].lower()

    async def test_a_read_is_not_described_as_a_change(self):
        card = await _card(_tool("GMAIL_FETCH_EMAILS", name="Gmail — Fetch"))
        assert "undone" not in card["effect"].lower()

    async def test_the_effect_names_the_app(self):
        card = await _card(_tool("GMAIL_SEND_EMAIL", name="Gmail — Send Email"))
        assert "gmail" in card["effect"].lower()


class TestTheModelDoesNotWriteIt:
    async def test_why_cannot_replace_the_effect(self):
        """``why`` is the model's sentence and stays its own. The effect is a
        separate field, so a card carries both and a reader can see them
        disagree."""
        card = await _card(_tool("GMAIL_REPLY_TO_THREAD", name="Gmail — Reply"))
        assert card["why"] == "Asked in the thread"
        assert card["effect"] != card["why"]

    async def test_the_model_cannot_supply_an_effect(self):
        """An effect passed in arguments is ignored: it is derived, or it is
        worth nothing."""
        tool = _tool("GMAIL_SEND_EMAIL", name="Gmail — Send Email")
        with patch.object(
            actions.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
        ):
            card = await actions.resolve(
                organization_id=1,
                workflow_id=None,
                arguments={
                    "action": actions.RUN_TOOL,
                    "why": "Asked in the thread",
                    "tool_uuid": tool.tool_uuid,
                    "arguments": {},
                    "effect": "This is only a draft, nothing sends.",
                },
            )
        assert "undone" in card["effect"].lower()
