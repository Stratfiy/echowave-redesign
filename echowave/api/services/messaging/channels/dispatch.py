"""One inbound message from any app, handled the same way (DCH-1).

1. A button tap settles the card, as the member who tapped, and only a card
   of that member's own organisation.
2. A message from an app identity nobody has linked is either a link code
   (and links it) or gets "link me first" and nothing else.
3. Any other line goes to Decibyl as that member, with ``reply_to`` naming
   the app, so the answer and any cards come back there too.

Outbound is ``deliver``: called by the worker after Decibyl's turn with the
reply text and the cards the turn proposed.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from api.services import features

from . import identities
from .base import SLACK, TEAMS, TELEGRAM, WHATSAPP, Card, Inbound

LINK_FIRST = (
    "Hi, I'm Decibyl. I don't know who you are yet. Open Decibyl → Settings → "
    "Decibyl in your apps, press Connect, and send me the code shown there."
)

_PLATFORM_FLAG = {
    TELEGRAM: "decibyl_telegram",
    SLACK: "decibyl_slack",
    TEAMS: "decibyl_teams",
}


def channel_on(channel: str, organization_id: int | None = None) -> bool:
    """The feature, and for Telegram/Slack/Teams that platform too."""
    if not features.is_on("decibyl_channels", organization_id):
        return False
    flag = _PLATFORM_FLAG.get(channel)
    return features.is_on(flag, organization_id) if flag else True


def adapter_for(channel: str):
    if channel == WHATSAPP:
        from .whatsapp import ADAPTER
    elif channel == TELEGRAM:
        from .telegram import ADAPTER
    elif channel == SLACK:
        from .slack import ADAPTER
    elif channel == TEAMS:
        from .teams import ADAPTER
    else:
        raise KeyError(channel)
    return ADAPTER


def card_from_payload(event_id: int, payload: dict[str, Any]) -> Card:
    return Card(
        event_id=int(event_id),
        label=str(payload.get("label") or "An action"),
        effect=str(payload.get("effect") or ""),
        state=str(payload.get("state") or "proposed"),
        reversible=bool(payload.get("reversible")),
        version=str(payload.get("version") or ""),
    )


async def _may_greet_stranger(adapter, inbound: Inbound) -> bool:
    """Whether an unlinked sender gets "link me first".

    Not ``channel_on(channel)`` with no organisation: the feature is usually on
    for named organisations only (``FEATURE_ORG_OVERRIDES``) and off globally,
    so that check said no to every stranger and they heard nothing at all.

    A message that reached us came through a platform we set up, so the
    adapter being configured is the gate. Slack knows one better: the
    workspace was added by an organisation, and that organisation's switch
    decides. Group chats never get here; each ``parse`` keeps to direct ones.
    """
    if not adapter.enabled():
        return False
    if inbound.channel == SLACK:
        team_id = str((inbound.ref or {}).get("team_id") or "")
        if team_id:
            from . import slack

            organization_id = await slack.installed_organization(team_id)
            if organization_id is not None:
                return channel_on(SLACK, organization_id)
    return True


async def handle(inbound: Inbound) -> str:
    """Route one normalised message. Returns a status word for the log."""
    from api.services.identity import channel_health

    # Every caller has checked the platform's signature before this.
    await channel_health.saw_verified_inbound(inbound.channel)
    adapter = adapter_for(inbound.channel)
    identity = await identities.find(inbound.channel, inbound.external_id)

    if identity is None:
        code = identities.code_in(inbound.text)
        if code is None:
            if await _may_greet_stranger(adapter, inbound):
                await adapter.send_text(inbound.ref, LINK_FIRST)
            return "unlinked"
        try:
            linked = await identities.redeem(
                code=code,
                channel=inbound.channel,
                external_id=inbound.external_id,
                display_name=inbound.display_name,
                conversation_ref=inbound.ref,
            )
        except identities.LinkError as exc:
            await adapter.send_text(inbound.ref, str(exc))
            return "bad_code"
        await adapter.send_text(
            inbound.ref,
            "Linked. I'm Decibyl, and I'll answer you here as you. Ask me "
            "anything; when something needs your OK you'll get buttons.",
        )
        logger.info(
            "Linked {} identity {} to org {} user {}",
            inbound.channel,
            linked.id,
            linked.organization_id,
            linked.user_id,
        )
        return "linked"

    if not channel_on(inbound.channel, identity.organization_id):
        return "switched_off"
    await identities.remember_ref(identity, inbound.ref)

    if inbound.tap is not None:
        return await _settle(adapter, identity, inbound)

    text = (inbound.text or "").strip()
    if not text:
        return "empty"
    from api.services.workflow import decibyl

    await decibyl.ask(
        organization_id=identity.organization_id,
        user_id=identity.user_id,
        text=text,
        attachments=[],
        line=text,
        preset=None,
        reply_to={
            "channel": inbound.channel,
            "to": inbound.external_id,
            "ref": inbound.ref or identity.conversation_ref,
        },
    )
    return "accepted"


async def _settle(adapter, identity: identities.Identity, inbound: Inbound) -> str:
    from api.services.refused import Refused
    from api.services.workflow import actions

    tap = inbound.tap
    try:
        # The version the button carried, when it carried one: a Confirm in
        # an app approves what that app showed (task ledger).
        versioned = {"version": tap.version} if tap.version is not None else {}
        payload = await actions.settle(
            organization_id=identity.organization_id,
            event_id=tap.event_id,
            verb=tap.verb,
            user_id=identity.user_id,
            **versioned,
        )
    except (actions.ActionError, Refused) as exc:
        # Includes a card of another organisation: settle only finds
        # proposals in the identity's own organisation.
        await adapter.acknowledge(inbound, str(exc))
        await adapter.send_text(inbound.ref, str(exc))
        return "refused"
    card = card_from_payload(tap.event_id, payload or {})
    await adapter.acknowledge(inbound, card.headline())
    await adapter.send_card(inbound.ref or identity.conversation_ref, card)
    return tap.verb


async def deliver(
    *,
    organization_id: int,
    reply_to: dict[str, Any],
    body: str,
    cards: list[Card],
) -> None:
    """Decibyl's answer, and its cards, back on the app it was asked on.
    Never raises: the answer is already on the thread."""
    channel = str(reply_to.get("channel") or "")
    try:
        adapter = adapter_for(channel)
    except KeyError:
        return
    ref = dict(reply_to.get("ref") or {})
    ref.setdefault("to", reply_to.get("to"))
    ref.setdefault("organization_id", organization_id)
    from api.services.identity import channel_health

    try:
        sent = []
        if body.strip():
            sent.append(await adapter.send_text(ref, body))
        for card in cards:
            sent.append(await adapter.send_card(ref, card))
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not deliver Decibyl's answer on {}: {}", channel, exc)
        await channel_health.delivery_failed(channel, type(exc).__name__)
        return
    if sent and all(sent):
        await channel_health.delivered(channel)
    elif sent:
        await channel_health.delivery_failed(channel, "refused")
