"""The ``browse`` tool: what Decibyl holds to open its private browser.

Offered only while ``decibyl_browser`` is on for the organisation, and only
to a turn that knows who asked: a browser belongs to a person, and a turn
with no person (a routine, a channel message from a stranger) has nobody
to own it, approve its steps or take it over.
"""

from __future__ import annotations

from typing import Any

from api.services import features
from api.services.browser import gate, session

TOOL_NAME = "browse"
FLAG = "decibyl_browser"

RULE = (
    f"- {TOOL_NAME}: Decibyl's private browser, for what web_search and "
    "web_fetch cannot do -- finding the cheapest of something across named "
    "sites, filling a form, checking a bill or a booking on a site the "
    "person uses. It opens an isolated browser for this person that they "
    "watch live in the thread and can take over (to sign in or solve a "
    "CAPTCHA; you never type passwords or codes). It asks on a card before "
    "anything that submits, pays, sends, books or signs up, and only for "
    "what the person's own words asked for. Pass the task in full, the "
    "sites by name, and the verbs the person asked for. Say it has started, "
    "then end your reply; the result arrives on the thread.\n"
)


def enabled(organization_id: int | None) -> bool:
    return features.is_on(FLAG, organization_id)


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Open Decibyl's private browser for the person and do a task on "
            "real websites: compare prices across sites, fill a form, check "
            "a bill or an order. The person watches it live and can take "
            "over. Anything that submits, pays, sends, books or signs up "
            "waits for their approval on a card. Runs in the background; "
            "the result comes to this thread."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "What the browser should do, in full, as the person "
                        "asked it: what to find or fill, what counts as done, "
                        "and what to report back."
                    ),
                },
                "sites": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "The sites the task is about, e.g. ['amazon.in', "
                        "'flipkart.com']. The browser stays on them. Leave "
                        "empty only when the person named none."
                    ),
                },
                "start_url": {
                    "type": "string",
                    "description": "A page to open first, when the person gave one.",
                },
                "may": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(gate.VERBS)},
                    "description": (
                        "What the person asked to have done, besides looking: "
                        "submit, pay, send, book, sign_up. Empty for a task "
                        "that only finds or checks something."
                    ),
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Fewer steps than the default, for a small task.",
                },
                "max_minutes": {
                    "type": "integer",
                    "description": "Fewer minutes than the default, for a small task.",
                },
            },
            "required": ["task"],
        },
    }


async def for_thread(
    organization_id: int,
    arguments: dict[str, Any],
    *,
    author_id: int | None,
    request: str,
    thread_id: str | None,
) -> dict[str, Any]:
    """The tool call. Never raises."""
    if not enabled(organization_id):
        return {"status": "unavailable", "reason": "the private browser is not on here"}
    if not author_id:
        return {
            "status": "unavailable",
            "reason": (
                "A browser belongs to the person who asks for it, and this line "
                "has no signed-in person. Say so in one line."
            ),
        }
    raw_sites = arguments.get("sites") or []
    if isinstance(raw_sites, str):
        raw_sites = [raw_sites]
    try:
        return await session.start(
            organization_id=organization_id,
            user_id=int(author_id),
            thread_id=thread_id,
            task=str(arguments.get("task") or ""),
            request=request,
            sites_named=[str(s) for s in raw_sites if str(s).strip()],
            verbs_declared=[str(v) for v in (arguments.get("may") or [])],
            start_url=str(arguments.get("start_url") or "").strip() or None,
            steps=arguments.get("max_steps"),
            minutes=arguments.get("max_minutes"),
        )
    except session.StartRefused as exc:
        return {"status": "refused", "reason": str(exc)}
