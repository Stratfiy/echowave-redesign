"""Decibyl's outreach tools in Chat (flag ``outreach``).

* ``find_leads`` -- a read. Criteria in, people with verified work addresses
  out, from the lead-data provider in ``leads``. With no key it answers
  ``needs_setup`` and puts a key form on the thread (``ask_for_key``): the
  owner adds the key where they already are, and the answer to "find me
  leads" is never an empty list that looks like "nobody matched".
* ``draft_outreach`` -- one send card per lead, on the person's own
  connected mailbox (Gmail or Outlook), carrying exactly the recipient, the
  subject and the body; the card's preview shows them and Confirm approves
  that version. Nothing is sent without Confirm. Leads already written to,
  unsubscribed, bounced or declined are skipped and named. With no mailbox
  connected it puts a connect card on the thread and hands the drafts back
  so the model can show them, rather than proposing cards that cannot run.

Uploaded lists and pasted leads need no tool of their own: an attached CSV
or Excel file reaches the model as text (and as a table, with
``table_tools``), and pasted lines are in the message. The rules below tell
the model to pick, say why, and draft from them the same way.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from api.enums import AgentEventActor, AgentEventKind, CostComponent
from api.services import features
from api.services.outreach import leads

FLAG = "outreach"

FIND_TOOL_NAME = "find_leads"
DRAFT_TOOL_NAME = "draft_outreach"
NAMES = frozenset({FIND_TOOL_NAME, DRAFT_TOOL_NAME})
#: A search keeps the tools open: the next step is saving and drafting.
READS = frozenset({FIND_TOOL_NAME})

#: Most drafts one call turns into cards. The same page Confirm all takes.
MAX_DRAFTS = 25
MAX_SUBJECT_CHARS = 200
MAX_BODY_CHARS = 5_000

#: The connected send tools a draft can go out on, by Composio slug, with the
#: argument names each takes for the recipient, the subject and the body.
#: Read in order: the first one connected is the mailbox used.
SEND_TOOLS: dict[str, dict[str, str]] = {
    "GMAIL_SEND_EMAIL": {"to": "recipient_email", "subject": "subject", "body": "body"},
    "OUTLOOK_SEND_EMAIL": {"to": "to_email", "subject": "subject", "body": "body"},
    "OUTLOOK_OUTLOOK_SEND_EMAIL": {
        "to": "to_email",
        "subject": "subject",
        "body": "body",
    },
}
#: The app the connect card offers when no mailbox is connected.
DEFAULT_MAILBOX_APP = "gmail"

#: Prospect statuses that mean "do not write again" (prospects.STATUSES).
DO_NOT_WRITE = frozenset({"unsubscribed", "bounced", "declined", "not_interested"})

RULES = (
    "- Outreach. Three ways in, one way out. (a) A website or a description "
    "of the business: read the site with web_fetch, say in two or three "
    "lines who its ideal customers are (role, kind of company, size, "
    "place), then call find_leads with those criteria. (b) A list attached "
    "as CSV or Excel: read it (rank_table when it is long), pick the rows "
    "that fit and say for each, in one line, why. (c) Leads pasted into "
    "the message: the same, from the lines given. Save the ones picked "
    "with save_prospects. Then draft_outreach: one short email per lead, "
    "written from what you know about them and the business -- never a "
    "template with the name swapped in -- with a plain subject. Each "
    "becomes a card showing exactly the recipient and the text; nothing is "
    "sent until the person confirms, one by one or with Confirm all. "
    "Never claim an email was sent. Offer a follow-up: schedule_routine "
    "to check, after the days they choose, who has not replied and draft "
    "one follow-up each with draft_outreach.\n"
    "- find_leads answers needs_setup when no lead-data key is added: say "
    "which provider, that a form to add the key is on the thread, and stop. "
    "Never invent leads, addresses or companies, and never guess an email.\n"
)

DESCRIPTION_FIND = (
    "Find people who match the business's ideal customer, with verified work "
    "emails, from the lead-data provider. Runs now. Give job titles, places, "
    "industries or keywords, and company sizes, from what the business sells "
    "and to whom. Returns each lead's name, title, company, website, email "
    "and city."
)
DESCRIPTION_DRAFT = (
    "Turn written emails into send cards, one per lead, on the person's own "
    "connected mailbox. Each card shows exactly the recipient, subject and "
    "body; nothing is sent until they confirm. Leads already written to, "
    "unsubscribed or bounced are skipped and named."
)


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


def schemas(organization_id: int | None = None) -> list[dict[str, Any]]:
    if not enabled(organization_id):
        return []
    words = {"type": "array", "items": {"type": "string"}}
    return [
        {
            "name": FIND_TOOL_NAME,
            "description": DESCRIPTION_FIND,
            "parameters": {
                "type": "object",
                "properties": {
                    "titles": {
                        **words,
                        "description": "Job titles to look for: 'Clinic owner', 'Practice manager'.",
                    },
                    "locations": {
                        **words,
                        "description": "Cities, states or countries: 'Pune, India'.",
                    },
                    "industries": {
                        **words,
                        "description": "Industries or kinds of business: 'dental clinics'.",
                    },
                    "keywords": {
                        "type": "string",
                        "description": "Anything else that marks a good fit.",
                    },
                    "company_sizes": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(leads.COMPANY_SIZES)},
                        "description": "Headcount ranges.",
                    },
                    "domains": {
                        **words,
                        "description": "Only these companies' websites, when named.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": f"How many, at most {leads.MAX_LEADS}. Default 10.",
                    },
                    "why": {
                        "type": "string",
                        "description": "One line: the ideal customer these criteria describe.",
                    },
                },
            },
        },
        {
            "name": DRAFT_TOOL_NAME,
            "description": DESCRIPTION_DRAFT,
            "parameters": {
                "type": "object",
                "properties": {
                    "emails": {
                        "type": "array",
                        "description": f"Up to {MAX_DRAFTS}, one per lead.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "to": {"type": "string", "description": "Their email."},
                                "name": {"type": "string"},
                                "company": {"type": "string"},
                                "subject": {"type": "string"},
                                "body": {
                                    "type": "string",
                                    "description": "The whole email, as it will be sent.",
                                },
                                "why": {
                                    "type": "string",
                                    "description": "One line: why this lead, for the card.",
                                },
                                "follow_up": {
                                    "type": "boolean",
                                    "description": "True when this is a follow-up to an earlier email.",
                                },
                            },
                            "required": ["to", "subject", "body"],
                        },
                    },
                },
                "required": ["emails"],
            },
        },
    ]


async def run(
    name: str,
    *,
    organization_id: int,
    arguments: dict[str, Any],
    user_id: int | None = None,
) -> dict[str, Any]:
    """The tool call. Never raises: the thread must keep answering."""
    try:
        if name == FIND_TOOL_NAME:
            return await find(organization_id, arguments, user_id=user_id)
        if name == DRAFT_TOOL_NAME:
            return await draft(organization_id, arguments)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Outreach tool {} failed for org {}", name, organization_id)
        return {"status": "error", "reason": f"That did not work: {exc}"}
    return {"status": "error", "reason": f"No outreach tool called {name}."}


# --- find_leads ---------------------------------------------------------------


async def find(
    organization_id: int, arguments: dict[str, Any], *, user_id: int | None = None
) -> dict[str, Any]:
    criteria = leads.Criteria.from_arguments(arguments)
    if criteria.empty():
        return {
            "status": "error",
            "reason": (
                "Say who to look for: job titles, places, industries or "
                "keywords from what the business sells and to whom."
            ),
        }
    provider = leads.provider()
    key = await leads.key_for(organization_id, provider.name)
    if not key.usable:
        await ask_for_key(organization_id, provider)
        return {
            "status": "needs_setup",
            "provider": provider.label,
            "reason": (
                f"No {provider.label} key is added, so no leads were looked up "
                "(this is not 'nobody matched'). A form to add the key is on "
                "the thread; it is stored with the workspace's provider keys, "
                "never in this chat. Say so in one line and stop. An operator "
                f"can also add a platform {provider.label} key under Provider "
                "keys, component data."
            ),
        }
    try:
        result = await provider.search(key.value or "", criteria)
    except leads.LeadError as exc:
        return {"status": "error", "provider": provider.label, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 - network and the like
        logger.warning("Lead search on {} failed: {}", provider.name, exc)
        return {
            "status": "error",
            "provider": provider.label,
            "reason": f"Could not reach {provider.label} just now. Try again shortly.",
        }
    charged = await _charge(organization_id, key, len(result.leads), user_id)
    out: dict[str, Any] = {
        "status": "success",
        "provider": provider.label,
        "leads": [lead.as_dict() for lead in result.leads],
        "count": len(result.leads),
        "charged": charged,
    }
    if result.total is not None:
        out["total_matching"] = result.total
    if result.without_email:
        out["left_out"] = (
            f"{result.without_email} more matched without a verified work "
            "email and are not listed."
        )
    if not result.leads:
        out["note"] = (
            f"{provider.label} found nobody with a verified email for these "
            "criteria. Say so and suggest wider ones (more titles, a larger "
            "area, fewer keywords)."
        )
    return out


async def _charge(
    organization_id: int, key: leads.Key, verified: int, user_id: int | None
) -> dict[str, Any]:
    from api.services.billing import lookup_source

    source = lookup_source.LookupSource(
        lookup_source.OWN if key.kind == leads.OWN else lookup_source.PLATFORM,
        key.provider,
    )
    try:
        return await lookup_source.charge(
            organization_id=organization_id,
            source=source,
            verified=verified,
            ref_id=f"find_leads:{organization_id}:{user_id or 0}:{_now_key()}",
            note="lead search",
        )
    except Exception as exc:  # noqa: BLE001 - the leads are found regardless
        logger.warning(
            "Could not charge a lead search for org {}: {}", organization_id, exc
        )
        return {"charged": False, "note": "The charge could not be recorded."}


def _now_key() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")


#: How many recent thread rows are read to see whether a key form for this
#: provider is already waiting, so a second search does not stack a second.
_RECENT_ROWS = 30


async def ask_for_key(organization_id: int, provider: leads.LeadProvider) -> bool:
    """Put a key form for ``provider`` on Decibyl's thread, unless one is
    already waiting. Returns whether a new one was written."""
    from api.db import db_client
    from api.services.workflow import agent_timeline

    try:
        recent = await db_client.agent_events(
            organization_id=organization_id,
            kinds=[AgentEventKind.NEEDS_SECRET.value],
            limit=_RECENT_ROWS,
            assistant_thread=True,
        )
    except Exception:  # noqa: BLE001 - at worst, a second form
        recent = []
    for row in recent:
        payload = dict(getattr(row, "payload", None) or {})
        target = payload.get("provider_key") or {}
        if target.get("provider") == provider.name and not payload.get("provided"):
            return False
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.NEEDS_SECRET.value,
        actor=AgentEventActor.AGENT.value,
        summary=f"Needs a {provider.label} API key",
        payload={
            "name": f"{provider.label} API key",
            "why": (
                f"Finding leads uses {provider.label}. {provider.key_help} It is "
                "kept with this workspace's provider keys and checked with "
                f"{provider.label} before it is saved."
            ),
            "credential_type": "provider_key",
            "fields": [{"key": "api_key", "label": "API key", "secret": True}],
            "provider_key": {
                "component": CostComponent.DATA.value,
                "provider": provider.name,
                "label": provider.label,
            },
            "stored_in": "Provider keys",
        },
        in_channel=False,
    )
    return True


# --- draft_outreach -------------------------------------------------------------

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.IGNORECASE)


async def _mailbox(organization_id: int) -> tuple[Any, dict[str, str]] | None:
    """The first connected send tool and its argument names, or None."""
    from api.services.workflow import connected_tools

    tools = await connected_tools.list_for_organization(organization_id)
    by_slug: dict[str, Any] = {}
    for tool in tools:
        slug = (connected_tools.slug_of(tool) or "").upper()
        if slug in SEND_TOOLS and connected_tools.is_connected(tool):
            by_slug.setdefault(slug, tool)
    for slug, names in SEND_TOOLS.items():
        if slug in by_slug:
            return by_slug[slug], names
    return None


async def _do_not_write(organization_id: int, addresses: list[str]) -> dict[str, str]:
    """Of these addresses, the ones not to write a first email to, and why:
    marked unsubscribed, bounced, declined or not interested, or already
    written to (``send_approval.note_sent`` counts the emails)."""
    from api.db import db_client

    if not addresses:
        return {}
    try:
        rows = await db_client.search_contacts_for_organization(
            organization_id, addresses, limit=len(addresses) * 3
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read prospects for org {}: {}", organization_id, exc)
        return {}
    wanted = {a.lower() for a in addresses}
    out: dict[str, str] = {}
    for row in rows:
        email = str(getattr(row, "email_normalized", None) or "").lower()
        if email not in wanted:
            continue
        attrs = getattr(row, "attributes", None)
        attrs = dict(attrs) if isinstance(attrs, dict) else {}
        status = str(attrs.get("status") or "").lower()
        if status in DO_NOT_WRITE:
            out[email] = status
            continue
        try:
            sent = int(attrs.get("emails_sent") or 0)
        except (TypeError, ValueError):
            sent = 0
        if sent > 0:
            out.setdefault(email, "already_emailed")
    return out


async def draft(organization_id: int, arguments: dict[str, Any]) -> dict[str, Any]:
    from api.services.workflow import actions, connector_offer, prospects

    raw = arguments.get("emails") or []
    if not isinstance(raw, list) or not raw:
        return {"status": "error", "reason": "Give one email per lead in `emails`."}
    if len(raw) > MAX_DRAFTS:
        return {
            "status": "error",
            "reason": f"At most {MAX_DRAFTS} at a time. Send the first {MAX_DRAFTS}.",
        }

    skipped: list[dict[str, str]] = []
    ready: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        to = str(item.get("to") or "").strip()
        subject = str(item.get("subject") or "").strip()[:MAX_SUBJECT_CHARS]
        body = str(item.get("body") or "").strip()[:MAX_BODY_CHARS]
        if not _EMAIL.match(to):
            skipped.append({"to": to, "reason": "not an email address"})
            continue
        junk = prospects.junk_reason(to)
        if junk:
            skipped.append({"to": to, "reason": junk})
            continue
        if not subject or not body:
            skipped.append({"to": to, "reason": "needs a subject and a body"})
            continue
        if to.lower() in seen:
            skipped.append({"to": to, "reason": "listed twice"})
            continue
        seen.add(to.lower())
        ready.append(
            {
                "to": to,
                "subject": subject,
                "body": body,
                "name": str(item.get("name") or "").strip()[:120],
                "company": str(item.get("company") or "").strip()[:120],
                "why": str(item.get("why") or "").strip()[:200],
                "follow_up": bool(item.get("follow_up")),
            }
        )

    blocked = await _do_not_write(organization_id, [i["to"] for i in ready])
    kept: list[dict[str, Any]] = []
    for item in ready:
        status = blocked.get(item["to"].lower())
        if status in DO_NOT_WRITE:
            skipped.append({"to": item["to"], "reason": f"marked {status}"})
        elif status == "already_emailed" and not item["follow_up"]:
            skipped.append(
                {
                    "to": item["to"],
                    "reason": "already written to; send it as a follow-up if meant",
                }
            )
        else:
            kept.append(item)

    if not kept:
        return {"status": "nothing_to_send", "skipped": skipped}

    mailbox = await _mailbox(organization_id)
    if mailbox is None:
        offer = await connector_offer.offer(
            organization_id=organization_id,
            arguments={
                "app": DEFAULT_MAILBOX_APP,
                "why": "To send the outreach emails from your own mailbox.",
            },
        )
        return {
            "status": "needs_connection",
            "reason": (
                "No mailbox is connected, so no send cards were made. "
                + str(offer.get("note") or offer.get("reason") or "")
                + " Show the drafts below as text, say they become cards once "
                "a mailbox is connected, and stop."
            ),
            "drafts": kept,
            "skipped": skipped,
        }

    tool, names = mailbox
    proposed: list[dict[str, Any]] = []
    for item in kept:
        who = ", ".join(p for p in (item["name"], item["company"]) if p)
        why = ("Follow-up" if item["follow_up"] else "Outreach") + (
            f" to {who}" if who else ""
        )
        if item["why"]:
            why += f": {item['why']}"
        result = await actions.propose(
            organization_id=organization_id,
            workflow_id=None,
            workflow_run_id=None,
            arguments={
                "action": actions.RUN_TOOL,
                "tool_uuid": tool.tool_uuid,
                "arguments": {
                    names["to"]: item["to"],
                    names["subject"]: item["subject"],
                    names["body"]: item["body"],
                },
                "why": why,
            },
            in_channel=False,
        )
        if result.get("status") == "proposed":
            proposed.append({"to": item["to"]})
        else:
            skipped.append(
                {
                    "to": item["to"],
                    "reason": str(result.get("reason") or result.get("status")),
                }
            )
    return {
        "status": "proposed" if proposed else "not_proposed",
        "cards": len(proposed),
        "proposed": proposed,
        "skipped": skipped,
        "note": (
            f"{len(proposed)} send card(s) are on the thread, each with exactly "
            "the recipient and the text. Nothing is sent until the person "
            "confirms, one at a time or with Confirm all. Say that, name any "
            "skipped and why, offer a follow-up, and end your reply."
        ),
    }
