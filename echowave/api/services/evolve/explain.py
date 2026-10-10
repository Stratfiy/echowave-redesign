"""What a skill card says: the five things skills-and-context.md asks for.

"Each skill should explain: what it helps accomplish, with an example; what
information and app access it needs; what it produces; whether it can take
external actions; where it is enabled."

A skill a person remembered carries these as fields. A shipped skill is a
markdown file that never had them, so they are read off its text, and where
the text does not say, the card says that -- "Not stated in this skill" -- in
place of a blank (api/AGENTS.md: an absence cannot be reviewed).

The external-actions answer is not a guess for any skill: a portable skill
is a procedure and cannot act (services/skills/__init__.py). What it can do
is ask an agent to use the agent's own tools, which carry their own
approvals; the card says which kinds of action the text mentions.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

NOT_STATED = "Not stated in this skill"

#: Apps a procedure may name, as the card shows them.
APPS: tuple[tuple[str, str], ...] = (
    (r"\bgmail\b", "Gmail"),
    (r"\bgoogle calendar\b|\bcalendar\b", "Calendar"),
    (r"\bgoogle sheets?\b|\bspreadsheets?\b", "Sheets"),
    (r"\bgoogle drive\b|\bdrive\b", "Drive"),
    (r"\bslack\b", "Slack"),
    (r"\bwhatsapp\b", "WhatsApp"),
    (r"\bhubspot\b", "HubSpot"),
    (r"\bsalesforce\b", "Salesforce"),
    (r"\bnotion\b", "Notion"),
    (r"\bshopify\b", "Shopify"),
    (r"\brazorpay\b", "Razorpay"),
    (r"\bstripe\b", "Stripe"),
    (r"\bzoho\b", "Zoho"),
    (r"\bjira\b", "Jira"),
    (r"\bgithub\b", "GitHub"),
    (r"\blinkedin\b", "LinkedIn"),
    (r"\bcrm\b", "a CRM"),
)

#: What a procedure may ask an agent to do outside Decibyl.
ACTIONS: tuple[tuple[str, str], ...] = (
    (r"\bsend(?:s|ing)?\b|\bemail(?:s|ing)?\b", "send messages"),
    (r"\bpost(?:s|ing)?\b|\bpublish(?:es|ing)?\b", "post or publish"),
    (r"\bbook(?:s|ing)?\b|\bschedul(?:e|es|ing)\b", "book or schedule"),
    (r"\bcall(?:s|ing)?\b", "make calls"),
    (r"\bpay(?:s|ing|ment)?\b|\binvoice\b|\brefund\b", "handle payments"),
)

#: What it produces, by the words its title and description use.
PRODUCES: tuple[tuple[str, str], ...] = (
    (r"\breport\b|\bbrief\b|\bsummary\b|\bsummar", "A written report or summary"),
    (r"\bemail\b|\boutreach\b|\bfollow[- ]?up\b", "Message or email drafts"),
    (r"\barticle\b|\bpost\b|\bcontent\b|\bcopy\b|\bnewsletter\b", "Written content"),
    (r"\bplan\b|\broadmap\b|\bstrategy\b", "A plan"),
    (r"\baudit\b|\breview\b|\bcheck\b", "A list of findings"),
    (r"\bprofile\b|\bvoice\b", "A profile to reuse"),
    (
        r"\bquiz\b|\bpractice\b|\bexplain\b|\bteach\b",
        "Explanations and practice questions",
    ),
)


def _first_sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    return (match.group(1) if match else text)[:240]


def _found(patterns: Iterable[tuple[str, str]], text: str) -> list[str]:
    seen: list[str] = []
    for pattern, label in patterns:
        if re.search(pattern, text, re.IGNORECASE) and label not in seen:
            seen.append(label)
    return seen


def _example_from_body(body: str) -> str:
    """The first bullet under a 'when' heading, as an example of use."""
    lines = (body or "").splitlines()
    under = False
    for line in lines:
        if line.startswith("#"):
            under = bool(re.search(r"when|use|activate|trigger", line, re.IGNORECASE))
            continue
        bullet = re.match(r"\s*[-*]\s+(.+)", line)
        if under and bullet:
            return _first_sentence(bullet.group(1))
    return ""


def explain(
    *,
    title: str,
    description: str,
    body: str,
    content: dict[str, Any] | None = None,
    on_agents: list[str] | None = None,
    installed: bool = False,
) -> dict[str, Any]:
    """The five answers, each a sentence or a short list, never empty."""
    content = content or {}
    text = f"{title}\n{description}\n{body}"
    accomplish = (
        content.get("description") or _first_sentence(description) or NOT_STATED
    )
    example = content.get("example") or _example_from_body(body)

    apps = _found(APPS, text)
    needs = list(content.get("inputs") or [])
    if not needs:
        needs = (
            [f"App access: {', '.join(apps)}"]
            if apps
            else ["No app access of its own; it works from what you give it"]
        )

    produces = list(content.get("outputs") or [])
    if not produces:
        produces = _found(PRODUCES, f"{title}\n{description}")[:2] or [
            "Written guidance in the chat"
        ]

    actions = _found(ACTIONS, text)
    acts = {
        "can_act": False,
        "sentence": (
            "No, not by itself: a skill is a procedure. "
            + (
                f"It may ask an agent to {', '.join(actions)}; that goes through "
                "the agent's own tools and their approval cards."
                if actions
                else "It does not ask an agent to act outside Decibyl."
            )
        ),
        "mentions": actions,
    }

    where = list(on_agents or [])
    if installed:
        where = ["Decibyl chat", *where]
    enabled_on = where or ["Not on any agent yet"]

    return {
        "accomplish": accomplish,
        "example": example or NOT_STATED,
        "needs": needs,
        "produces": produces,
        "external_actions": acts,
        "enabled_on": enabled_on,
        "wont_do": list(content.get("wont_do") or []),
    }


__all__ = ["NOT_STATED", "explain"]
