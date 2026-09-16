"""Decibyl offers an app, as a card on the thread.

Somebody asks Decibyl to read their Gmail and the true answer is "that
account is not connected yet". Until now that was the whole answer: a
sentence telling the person to leave the conversation, find the
Marketplace, find Tools, find the app, and press a button there. The
conversation they were having ended at the sentence, and most people do
not come back.

So the answer is a card instead. It carries what the Integrations screen
would have shown for that app -- the logo, the one line, how many tools it
brings -- and a button that starts the sign-in. Nothing is connected by
the card appearing: the link is minted when a person presses it, the
sign-in happens on the vendor's own screen, and an account connected there
is connected for the whole organisation, which is why the button is the
admin's to press and refuses with a plain sentence for anybody else.

Deliberately *not* an ``action_proposed``. A proposal's confirm arms a
ten-second undo window before anything runs, which is the right shape for
ringing somebody back and the wrong shape here: the press opens a sign-in
screen, and the consent this needs is the vendor's own, not a countdown.
The card is an offer, and the OAuth screen is the confirmation.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from api.enums import AgentEventActor, AgentEventKind
from api.services.integrations.composio import catalogue
from api.services.integrations.composio.client import (
    connected_toolkits,
    is_configured,
)
from api.services.workflow import agent_timeline

TOOL_NAME = "offer_connector"
#: A description that says what the tool does *and* what it does not, because
#: a model told only the first half offers Gmail and then reports it connected.
DESCRIPTION = (
    "Put a connect card for an outside app on the thread -- Gmail, Google "
    "Calendar, Outlook, Slack, HubSpot, a CRM -- when the person wants "
    "something that needs an app this account has not connected yet. The "
    "card shows the app and a Sign in button. It does NOT connect anything: "
    "a person has to press it and sign in on the vendor's own screen, and "
    "only an admin of this account can. Say the card is there and what "
    "signing in will let you do, then end your reply. Do not claim the app "
    "is connected, and do not tell anybody to go to the Marketplace -- the "
    "card is the way."
)

#: How many cards one reply may put up. A model asked for "email and
#: calendar" reasonably wants two; a model that wants six has misunderstood
#: the question and six cards is a wall, not an answer.
MAX_PER_TURN = 2


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": (
                        "The app's slug or name as a person would say it: "
                        "'gmail', 'googlecalendar', 'slack', 'hubspot'."
                    ),
                },
                "why": {
                    "type": "string",
                    "description": (
                        "One line on what signing in will let you do for "
                        "them, in their words. Shown under the app's name."
                    ),
                },
            },
            "required": ["app"],
        },
    }


MAX_WHY_CHARS = 200


def _match(rows: list[catalogue.Connector], wanted: str) -> catalogue.Connector | None:
    """The app somebody named, by slug first and then by name.

    Slug first because a model that has seen this catalogue writes the slug,
    and ``googlecalendar`` must not be beaten by a fuzzy name match on
    "Google Calendar Simple". A name match is a whole-word one: "mail"
    should find nothing rather than the first of forty apps with mail in
    the middle of their description.
    """
    key = wanted.strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    if not key:
        return None
    for row in rows:
        if row.slug.lower().replace("_", "").replace("-", "") == key:
            return row
    for row in rows:
        if row.name.lower().replace(" ", "") == key:
            return row
    for row in rows:
        if key in row.name.lower().replace(" ", ""):
            return row
    return None


async def offer(*, organization_id: int, arguments: dict[str, Any]) -> dict[str, Any]:
    """Post one connect card. Returns the sentence the model is told.

    Every refusal names itself. A model told only "no" offers the same app
    again on the next line; a model told "that one is already connected"
    goes and uses it.
    """
    wanted = str(arguments.get("app") or "").strip()
    why = str(arguments.get("why") or "").strip()[:MAX_WHY_CHARS]
    if not wanted:
        return {"status": "not_offered", "reason": "Say which app."}
    if not is_configured():
        return {
            "status": "not_offered",
            "reason": (
                "Connecting outside apps is not switched on for this "
                "platform. Say so plainly rather than offering it."
            ),
        }

    rows = await catalogue.connectors()
    if not rows:
        return {
            "status": "not_offered",
            "reason": (
                "The app catalogue could not be read just now. Say so and "
                "offer to try again."
            ),
        }
    app = _match(rows, wanted)
    if app is None:
        return {
            "status": "not_offered",
            "reason": f"There is no app called {wanted!r} to connect.",
        }

    try:
        already = {t.upper() for t in await connected_toolkits(organization_id)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read connected apps for the offer: {}", exc)
        already = set()
    if app.slug.upper() in already:
        return {
            "status": "already_connected",
            "reason": (
                f"{app.name} is already connected to this account -- use it "
                "rather than offering it again."
            ),
        }
    # A vendor we integrate ourselves (a carrier, a payment gateway) is set up
    # on its own screen: connecting it here would store credentials no call
    # path reads. Said in as many words rather than dropped, so the model
    # sends the person to the right place instead of to a card that lies.
    if app.setup == catalogue.SETUP_OURS and not app.also_connectable:
        return {
            "status": "not_offered",
            "reason": (
                f"{app.name} is set up on its own screen here"
                + (f" ({app.setup_url})" if app.setup_url else "")
                + ", not as a connector."
            ),
        }

    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.CONNECTOR_OFFERED.value,
        actor=AgentEventActor.AGENT.value,
        summary=f"Connect {app.name}",
        payload={
            "app": app.slug,
            "name": app.name,
            "description": app.description,
            "logo": app.logo,
            "tools_count": app.tools_count,
            "why": why,
        },
        in_channel=False,
    )
    return {
        "status": "offered",
        "note": (
            f"A connect card for {app.name} is on the thread. Say it is "
            "there and that signing in is theirs to do, then end your "
            f"reply. {app.name} is NOT connected yet."
        ),
    }
