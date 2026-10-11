"""Ask every customer for a review, from Chat, on one card.

Reviews are how a small business is found -- and asking at the right moment,
just after the customer was served, is the whole trick. ``ask_for_reviews``
proposes one card that adds a **Review request** message to an agent (or to
every agent): sent after each real conversation, to the number that was on
it, with the business's review link.

**Within Google's rules, by construction.** Google's review policy treats
selectively asking happy customers ("review gating") and offering anything in
return as fake engagement, and can take a business's reviews down for it. So
the message:

* goes to **everyone** the agent really spoke with -- never conditioned on a
  rating, a sentiment or an outcome (``send_when_*`` is left empty);
* offers nothing in return -- a message that mentions a discount, a coupon,
  cashback or a reward is turned back before any card is shown;
* never asks for a number of stars;
* goes **at most once per person** in ``ONCE_EVERY_DAYS``, and never after a
  missed call, a voicemail or a test call (``only_if_answered``).

The link can be any review page the business uses -- Google, Justdial,
Practo, Zomato, TripAdvisor -- and the card names which one it is.

**The channel.** WhatsApp refuses free text outside its 24-hour window, so a
WhatsApp review request uses Decibyl's approved review template
(``WHATSAPP_REVIEW_TEMPLATE``); without one, the request goes by SMS on the
account's own carrier, and the card says so rather than failing later.

Confirm puts the message in the agent's **draft**, like every change made for
an owner: live calls get it once the agent is published, and the card says so.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from api import constants
from api.db import db_client
from api.services import features

TOOL_NAME = "ask_for_reviews"
FLAG = "review_requests"
NODE_NAME = "Review request"
NODE_ID = "review-request"
ONCE_EVERY_DAYS = 90
ALL_AGENTS = "all"
MAX_MESSAGE_CHARS = 320

#: Decibyl's rule for this tool lives in ``decibyl.SYSTEM`` with the other
#: always-on tools, so the shared prompt does not change with this switch.

#: A reward for a review, in any of the ways people write one.
_INCENTIVE = re.compile(
    r"\b(discount|coupon|cash ?back|voucher|reward|gift|prize|lucky draw|"
    r"points?|free (?!to)\w+|\d+\s?% off|% off|rs\.? ?\d+ off|₹ ?\d+ off|"
    r"in return)\b",
    re.IGNORECASE,
)
#: Asking for a score, or only the satisfied.
_GATING = re.compile(
    r"\b(5|five)[- ]?stars?\b|\bif you (were|are) (happy|satisfied|pleased)|"
    r"\bonly if\b|\bif you (liked|loved|enjoyed)\b",
    re.IGNORECASE,
)

#: Where a review link points, by host, for the card's words.
PLATFORMS: tuple[tuple[str, str], ...] = (
    ("g.page", "Google"),
    ("goo.gl", "Google"),
    ("google.", "Google"),
    ("justdial.", "Justdial"),
    ("practo.", "Practo"),
    ("zomato.", "Zomato"),
    ("swiggy.", "Swiggy"),
    ("tripadvisor.", "TripAdvisor"),
    ("trustpilot.", "Trustpilot"),
    ("facebook.", "Facebook"),
    ("indiamart.", "IndiaMART"),
)


class ReviewSetupError(ValueError):
    """Words for the model: what is missing or not allowed."""


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Propose one card that makes an agent ask every customer it "
            "really spoke with for a review, by WhatsApp or SMS after the "
            f"conversation, at most once per person every {ONCE_EVERY_DAYS} "
            "days. Nothing changes until a person confirms; the agent sends "
            "once it is published."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "The agent by @handle or name, or 'all'.",
                },
                "review_link": {
                    "type": "string",
                    "description": (
                        "The business's review page, as the person gave it "
                        "(Google, Justdial, Practo...)."
                    ),
                },
                "business_name": {
                    "type": "string",
                    "description": "How the business is called in the message.",
                },
                "channel": {
                    "type": "string",
                    "enum": ["whatsapp", "sms"],
                    "description": "Leave out to use WhatsApp where it can be sent.",
                },
                "message": {
                    "type": "string",
                    "description": (
                        "Only if the person wrote their own words. It must "
                        "carry {link}; {business} is the business's name."
                    ),
                },
                "why": {"type": "string", "description": "One line, for the card."},
            },
            "required": ["agent", "review_link"],
        },
    }


# --- checks -----------------------------------------------------------------


def platform_of(link: str) -> str:
    host = (urlsplit(link).hostname or "").lower()
    for marker, name in PLATFORMS:
        if marker in host:
            return name
    return host.removeprefix("www.") or "the review page"


def check_link(raw: str) -> str:
    from api.services.workflow import web_tools

    link = (raw or "").strip()
    if link and "://" not in link:
        link = f"https://{link}"
    try:
        link = web_tools.check_address(link)
    except web_tools.FetchRefused as exc:
        raise ReviewSetupError(
            "That is not a review link I can send. Ask for the full link to "
            "their review page (Google: Business Profile, 'Ask for reviews')."
        ) from exc
    if urlsplit(link).scheme != "https":
        raise ReviewSetupError("The review link must start with https.")
    return link


def check_words(message: str) -> None:
    """Refuse a message Google would treat as fake engagement."""
    if _INCENTIVE.search(message):
        raise ReviewSetupError(
            "Google does not allow anything offered for a review (a "
            "discount, a gift, cashback, points) and takes such reviews "
            "down. Tell them, and propose the message without it."
        )
    if _GATING.search(message):
        raise ReviewSetupError(
            "Google does not allow asking for stars or asking only happy "
            "customers. Every customer is asked the same way; propose the "
            "message without it."
        )


def message_for(business: str, custom: str | None) -> str:
    """The message as it will be read, with ``{link}`` still to fill."""
    if custom:
        text = " ".join(str(custom).split())
        if "{link}" not in text:
            raise ReviewSetupError(
                "The message must carry the review link: put {link} where it goes."
            )
    else:
        text = (
            "Thank you for choosing {business}. If you have a minute, we "
            "would be grateful for your review: {link}"
        )
    text = text.replace("{business}", business)
    if len(text) > MAX_MESSAGE_CHARS:
        raise ReviewSetupError(
            f"Keep the message under {MAX_MESSAGE_CHARS} characters; it is a text."
        )
    return text


def channel_for(asked: str | None) -> tuple[str, str | None]:
    """(channel, note for the card). WhatsApp needs the approved template."""
    template = constants.WHATSAPP_REVIEW_TEMPLATE.strip()
    wanted = (asked or "").strip().lower()
    if wanted == "sms":
        return "sms", None
    if template:
        return "whatsapp", None
    if wanted == "whatsapp":
        raise ReviewSetupError(
            "WhatsApp only takes a message like this on an approved review "
            "template, and this workspace does not have one yet. Offer SMS "
            "instead, and say WhatsApp follows once the template is approved."
        )
    return "sms", "By SMS: WhatsApp follows once the review template is approved."


def node_data(*, link: str, business: str, text: str, channel: str) -> dict[str, Any]:
    """The Send Message step the card adds, as the editor stores it."""
    data: dict[str, Any] = {
        "name": NODE_NAME,
        "enabled": True,
        "channel": channel,
        "body": text.replace("{link}", link),
        "only_if_answered": True,
        "once_per_contact_days": ONCE_EVERY_DAYS,
    }
    if channel == "whatsapp":
        data.update(
            {
                "template_name": constants.WHATSAPP_REVIEW_TEMPLATE.strip(),
                "template_language": (
                    constants.WHATSAPP_REVIEW_TEMPLATE_LANGUAGE.strip() or "en"
                ),
                # Comma-separated by the step's own rule, so a comma in the
                # name would split it into two values.
                "template_params": f"{business.replace(',', ' ')},{link}",
                "template_category": "marketing",
            }
        )
    return data


# --- the card ---------------------------------------------------------------


async def _business_name(organization_id: int, given: str | None) -> str:
    name = " ".join(str(given or "").split())[:80]
    if name:
        return name
    org = await db_client.get_organization_by_id(organization_id)
    name = (getattr(org, "name", None) or "").strip()
    return name or "us"


async def resolve(*, organization_id: int, arguments: dict[str, Any]) -> dict[str, Any]:
    """The card: exactly what Confirm adds. Raises ReviewSetupError."""
    from api.services.workflow import office

    roster = list(
        await db_client.get_all_workflows_for_listing(organization_id=organization_id)
    )
    wanted = str(arguments.get("agent") or "").strip()
    if wanted.lower() in (ALL_AGENTS, "all agents", "every agent"):
        agents = roster
        if not agents:
            raise ReviewSetupError("There is no agent here yet; build one first.")
    else:
        agent = office._match_bot(wanted, roster)
        if agent is None:
            raise ReviewSetupError(
                f"No agent called {wanted!r} here; use its exact @handle, or 'all'."
            )
        agents = [agent]
    link = check_link(str(arguments.get("review_link") or ""))
    business = await _business_name(organization_id, arguments.get("business_name"))
    custom = arguments.get("message")
    if custom:
        # The person's words, before the business's own name is put in:
        # "Gift Corner" is a name, not a gift.
        check_words(str(custom))
    text = message_for(business, str(custom) if custom else None)
    channel, note = channel_for(arguments.get("channel"))
    if channel == "sms" and not await db_client.get_default_telephony_configuration(
        organization_id
    ):
        raise ReviewSetupError(
            "Review requests by SMS go out on this workspace's own phone "
            "carrier, and it has none yet. WhatsApp review requests follow "
            "once Decibyl's review template is approved. Say so plainly; do "
            "not propose the card."
        )
    data = node_data(link=link, business=business, text=text, channel=channel)

    names = ", ".join(a.name for a in agents[:6]) + (
        f" and {len(agents) - 6} more" if len(agents) > 6 else ""
    )
    lines = [
        f"Agent{'s' if len(agents) > 1 else ''}: {names}",
        f"Review page: {platform_of(link)} ({link})",
        f"Message ({'WhatsApp' if channel == 'whatsapp' else 'SMS'}): "
        + text.replace("{link}", link),
        "Who: everyone the agent really spoke with -- not after a missed "
        "call, a voicemail or a test",
        f"How often: at most once per person every {ONCE_EVERY_DAYS} days",
    ]
    if note:
        lines.append(note)
    return {
        "args": {
            "workflow_ids": [int(a.id) for a in agents],
            "node": data,
        },
        "label": (
            f"Ask {agents[0].name}'s customers for reviews"
            if len(agents) == 1
            else f"Ask customers for reviews on {len(agents)} agents"
        ),
        "preview": "\n".join(lines),
        "effect": (
            "Adds a Review request message to the agent's draft. It is sent "
            "once the agent is published; every customer is asked the same "
            "way, as Google requires."
        ),
        "reversible": False,
    }


def with_review_request(definition: dict[str, Any], data: dict[str, Any]) -> dict:
    """The definition with this review request in it: the one already there
    (by id or by name) replaced, not a second one added."""
    out = json.loads(json.dumps(definition or {}))
    nodes = [n for n in out.get("nodes") or [] if isinstance(n, dict)]
    kept = [
        n
        for n in nodes
        if n.get("id") != NODE_ID
        and str((n.get("data") or {}).get("name") or "").strip().lower()
        != NODE_NAME.lower()
    ]
    lowest = max(
        (float((n.get("position") or {}).get("y") or 0) for n in kept), default=0.0
    )
    kept.append(
        {
            "id": NODE_ID,
            "type": "sms",
            "position": {"x": 600.0, "y": lowest + 200.0},
            "data": dict(data),
        }
    )
    out["nodes"] = kept
    out.setdefault("edges", [])
    return out


async def execute(*, organization_id: int, payload: dict[str, Any]) -> str:
    """Do what the card showed: the step into each agent's draft."""
    from api.services.workflow.dto import SmsNodeData

    args = payload.get("args") or {}
    data = dict(args.get("node") or {})
    SmsNodeData.model_validate(data)  # a card that would not save is refused
    done: list[str] = []
    for workflow_id in args.get("workflow_ids") or []:
        workflow = await db_client.get_workflow(
            int(workflow_id), organization_id=organization_id
        )
        if workflow is None:
            continue
        draft = await db_client.get_draft_version(workflow.id)
        released = getattr(workflow, "released_definition", None)
        source = (
            draft.workflow_json
            if draft is not None
            else (
                released.workflow_json
                if released is not None
                else workflow.workflow_definition
            )
        )
        await db_client.save_workflow_draft(
            workflow.id, workflow_definition=with_review_request(source or {}, data)
        )
        done.append(workflow.name)
    if not done:
        raise ReviewSetupError("Those agents are not here any more.")
    return (
        f"{', '.join(done)} will ask customers for a review after each real "
        "conversation once published. Publish to switch it on."
    )


__all__ = [
    "NODE_NAME",
    "ONCE_EVERY_DAYS",
    "TOOL_NAME",
    "ReviewSetupError",
    "check_link",
    "check_words",
    "enabled",
    "execute",
    "message_for",
    "platform_of",
    "resolve",
    "tool_schema",
    "with_review_request",
]
