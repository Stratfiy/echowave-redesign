"""What a customer sees about a pack, all computed from its declaration.

The badge on the card, the price under it, the filters it answers to and the
steps somebody is walked through when they hire it. None of these are written
by the publisher, and that is deliberate: a badge a publisher can type is a
badge a publisher can get wrong, and the two ways of getting it wrong are
"customer hires a phone agent and gets text messages" and "we charge a seat for
something that never dials".

One rule decides the money: **does this pack make or take calls.** A calling
pack is a hire -- it replaces a person who answers the phone, and it is priced
like one. Everything else runs on the platform plan, where the marginal cost is
close enough to zero that metering it would only discourage use.
"""

from __future__ import annotations

from typing import Any

from api.services.packs._base import CALLING_CHANNELS, AgentPack, Channel

#: A voice agent, per month, per agent. Priced as a hire rather than as usage
#: because that is what it is: the alternative to this agent is a salaried
#: person answering the phone, and teammate pricing lands at 10-30% of the
#: salary it displaces.
SEAT_PRICE_PAISE = 699_900

#: The platform and every non-calling agent on it, per month, per business.
#: Not per agent: those cost nothing to run and the product is worth more the
#: more of them somebody has.
PLATFORM_PRICE_PAISE = 499_900

#: Minutes included with a seat, pooled across every voice agent in the
#: organisation rather than allocated per agent.
#:
#: Pooling is what stops the format being gamed. Per-agent minutes would push a
#: customer to cram the front desk, the order confirmation and the NDR chase
#: into one agent to avoid paying twice -- producing a worse agent, worse calls
#: and one meaningless status line instead of three useful ones.
INCLUDED_MINUTES_PER_SEAT = 1_200

#: Non-calling agent runs included with the platform plan, per month.
INCLUDED_EXECUTIONS = 10_000

#: What each channel is called on a card. A mapping rather than a prettified
#: enum value: "inbound_call" is structure, "Answers calls" is the promise, and
#: the promise is what somebody hires.
CHANNEL_LABELS: dict[Channel, str] = {
    Channel.INBOUND_CALL: "Answers calls",
    Channel.OUTBOUND_CALL: "Makes calls",
    Channel.WHATSAPP: "WhatsApp",
    Channel.EMAIL: "Email",
    Channel.SLACK: "Slack",
    Channel.TELEGRAM: "Telegram",
    Channel.WEB: "Web chat",
    Channel.SCHEDULED: "Scheduled",
}

#: Calling first, then the channels a person is most likely to be shopping for.
#: Fixed rather than sorted alphabetically, because the order on the card is an
#: argument about what matters.
_BADGE_ORDER: tuple[Channel, ...] = (
    Channel.INBOUND_CALL,
    Channel.OUTBOUND_CALL,
    Channel.WHATSAPP,
    Channel.EMAIL,
    Channel.WEB,
    Channel.SLACK,
    Channel.TELEGRAM,
    Channel.SCHEDULED,
)

STEP_HEAR_IT = "hear_it"
STEP_FACTS = "facts"
STEP_CONNECT = "connect"
STEP_NUMBER = "number"
STEP_GO_LIVE = "go_live"


def is_calling(pack: AgentPack) -> bool:
    """Whether hiring this puts an agent on a phone call."""
    return bool(set(pack.channels) & CALLING_CHANNELS)


def badges(pack: AgentPack) -> list[str]:
    """The capability chips on the card, in the order they should read.

    Every declared channel appears. A channel we have not given a label yet
    still appears, under its raw name -- a pack quietly losing a capability off
    its own card is the failure this codebase keeps having, and an ugly badge
    is a much smaller problem than an invisible one.
    """
    declared = set(pack.channels)
    ordered = [
        CHANNEL_LABELS[channel] for channel in _BADGE_ORDER if channel in declared
    ]
    unlabelled = sorted(
        CHANNEL_LABELS.get(channel, str(getattr(channel, "value", channel)))
        for channel in declared
        if channel not in _BADGE_ORDER
    )
    return ordered + unlabelled


def pricing(pack: AgentPack) -> dict[str, Any]:
    """What hiring this costs, and in what unit.

    Returned as structure rather than a formatted string: the card, the hire
    flow and the invoice all need this and they format it differently. The one
    thing they must not do is disagree about it.
    """
    calling = is_calling(pack)
    return {
        "is_hire": calling,
        "seat_price_paise": SEAT_PRICE_PAISE if calling else 0,
        "platform_price_paise": 0 if calling else PLATFORM_PRICE_PAISE,
        "creator_price_paise": pack.creator_price_paise,
        "monthly_price_paise": (
            (SEAT_PRICE_PAISE if calling else PLATFORM_PRICE_PAISE)
            + pack.creator_price_paise
        ),
        "included_minutes": INCLUDED_MINUTES_PER_SEAT if calling else 0,
        "included_executions": 0 if calling else INCLUDED_EXECUTIONS,
        #: The unit overage is billed in, so a card can say what happens next
        #: rather than leaving somebody to find out on an invoice.
        "overage_unit": "minute" if calling else "execution",
    }


def hire_steps(pack: AgentPack) -> list[dict[str, Any]]:
    """The guided flow for hiring this pack, generated from its declaration.

    Ordered the way the decision actually happens. Hearing it comes first: a
    voice agent sold on a screenshot is sold on a promise, and every form
    before the demo is a chance to leave.

    A step only appears when the pack needs it. A pack asking for nothing does
    not get an empty form, and one that takes no inbound calls is not asked to
    choose a phone number.
    """
    steps: list[dict[str, Any]] = []

    if is_calling(pack):
        steps.append(
            {
                "key": STEP_HEAR_IT,
                "title": "Hear it first",
                "detail": "Ring the demo number and talk to it before you set anything up.",
                "demo_number": pack.demo_number,
                "blocking": False,
            }
        )

    if pack.required_facts:
        steps.append(
            {
                "key": STEP_FACTS,
                "title": "Tell it about your business",
                "detail": "Answered once. Every agent you hire after this already knows.",
                "facts": [fact.model_dump(mode="json") for fact in pack.required_facts],
                "blocking": any(fact.required for fact in pack.required_facts),
            }
        )

    if pack.required_connectors:
        steps.append(
            {
                "key": STEP_CONNECT,
                "title": "Connect what it needs",
                "detail": "So the work it does lands where you already keep it.",
                "connectors": [
                    connector.model_dump(mode="json")
                    for connector in pack.required_connectors
                ],
                "blocking": any(
                    connector.required for connector in pack.required_connectors
                ),
            }
        )

    if Channel.INBOUND_CALL in pack.channels:
        steps.append(
            {
                "key": STEP_NUMBER,
                "title": "Give it a number",
                "detail": "Buy one, or point the number on your board at it.",
                "blocking": True,
            }
        )

    steps.append(
        {
            "key": STEP_GO_LIVE,
            "title": "Go live",
            "detail": "One switch. You can pause it any time, and a paused agent is not billed.",
            "blocking": False,
        }
    )
    return steps


def card(pack: AgentPack) -> dict[str, Any]:
    """Everything the shelf needs about one pack, in one shape."""
    return {
        "slug": pack.slug,
        "name": pack.name,
        "summary": pack.summary,
        "job": pack.job,
        "version": pack.version,
        "publisher": pack.publisher.model_dump(mode="json"),
        "badges": badges(pack),
        "industries": list(pack.industries),
        "languages": list(pack.languages),
        "demo_number": pack.demo_number,
        "pricing": pricing(pack),
        "listed": pack.listed,
    }
