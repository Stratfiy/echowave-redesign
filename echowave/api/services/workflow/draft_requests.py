"""When the person said draft, a send is not a card to offer.

#365 put the truth on the card: a derived line, beside the Confirm button,
saying what running the tool does. It works. What it does not do is stop the
model reaching for the wrong tool, and a SYSTEM rule telling it not to was
tried and did not change the behaviour -- asked for a draft and nothing sent,
Decibyl proposed GMAIL_REPLY_TO_THREAD again, and again described it as a
draft that sends nothing.

So this is the structural half. A prompt rule is advice; this is a refusal.
When the request said draft, or said not to send, a connected-app write that
is not a staged write never becomes a card at all. The model is handed back
the reason and the name of the tool it should have used, on the same
mechanism that already tells it to ask for a missing variable, so it can
correct itself inside the turn.

Read in the safe direction, deliberately. A false positive costs a round and
a draft instead of a send. A false negative is mail nobody meant to send.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from api.services.workflow import connected_tools, unattended

#: Ways of saying "do not send it". Absolute: these block a send whether or
#: not the app offers a draft, because the person has said the thing they do
#: not want and no tool choice makes that acceptable.
_DO_NOT_SEND = (
    r"do\s*n[o']?t\s+send",
    r"don'?t\s+send",
    r"without\s+sending",
    r"never\s+send",
    r"no\s+sending",
    r"not\s+to\s+send",
)

#: Ways of asking for a draft. Matched as a whole word so "redrafting the
#: contract" counts and "draughtsman" does not.
_WANTS_DRAFT = (
    r"(?<![a-z])drafts?(?![a-z])",
    r"(?<![a-z])drafting(?![a-z])",
)


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = (text or "").lower()
    return any(re.search(p, lowered) for p in patterns)


def said_do_not_send(text: str) -> bool:
    """Whether the request asked for nothing to be sent."""
    return _matches(text, _DO_NOT_SEND)


def wants_a_draft(text: str) -> bool:
    """Whether the request asked for a draft."""
    return _matches(text, _WANTS_DRAFT)


def draft_tool_for(tool: Any, tools: list[Any]) -> Optional[Any]:
    """The staged write for this tool's app, if the account has one.

    Same toolkit only. A draft in Gmail is no use to somebody asking for a
    draft in Zoho, and offering one would be its own wrong answer.
    """
    app = connected_tools.toolkit_of(tool)
    if not app:
        return None
    for candidate in tools:
        if connected_tools.toolkit_of(candidate) != app:
            continue
        if unattended.is_staged(candidate):
            return candidate
    return None


def refusal(*, text: str, tool: Any, tools: list[Any]) -> Optional[dict[str, Any]]:
    """The reason this tool must not be proposed, or None to go ahead.

    Returned as a tool result rather than raised, because the model reads
    tool results and can act on one in the same turn -- the way it already
    recovers from "ask the person for these first".
    """
    if connected_tools.is_read(tool) or unattended.is_staged(tool):
        return None
    asked_draft = wants_a_draft(text)
    if not asked_draft and not said_do_not_send(text):
        return None

    alternative = draft_tool_for(tool, tools)
    app = connected_tools.toolkit_of(tool) or "that app"
    if alternative is not None:
        return {
            "status": "refused",
            "reason": (
                f"You were asked not to send. {connected_tools.slug_of(tool)} "
                f"sends. Use {connected_tools.slug_of(alternative)} instead, "
                "which writes a draft nobody receives until a person sends it."
            ),
        }
    return {
        "status": "refused",
        "reason": (
            f"You were asked not to send, and {app} has no draft tool here, so "
            "there is nothing you can do that does not send. Say that to the "
            "person instead of proposing a send."
        ),
    }


__all__ = [
    "draft_tool_for",
    "refusal",
    "said_do_not_send",
    "wants_a_draft",
]
