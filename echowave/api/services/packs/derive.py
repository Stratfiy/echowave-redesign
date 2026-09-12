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

from typing import Any, Optional

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

#: Two flows, because hiring a voice agent and hiring a back-office agent are
#: different decisions. A voice agent is judged by how it sounds, so the
#: listening happens twice -- once on the published demo before any setup, and
#: again on your own configuration before it goes live. A back-office agent is
#: judged by what it does with your data, so the work is fitting it to the
#: business and the listening steps would be theatre.
FLOW_VOICE = "voice"
FLOW_STANDARD = "standard"

#: Hear the published role before touching a form. A voice agent sold on a
#: screenshot is sold on a promise.
STEP_INTERVIEW = "interview"
#: What the agent has to know about this business. Answered once for the
#: organisation, not once per agent.
STEP_ONBOARDING = "onboarding"
#: The apps it needs access to, framed as the job's requirements rather than
#: as a setup chore.
STEP_REQUIREMENTS = "requirements"
#: Which apps confirm what happened, once the call has ended, and the
#: credentials they need. Optional: plenty of agents are useful without it.
STEP_AFTER_CALL = "after_call"
#: Voice and brain: a named preset, priced per minute, not a model dropdown.
STEP_VOICE_AND_BRAIN = "voice_and_brain"
#: Hear your own configuration speak, instantly, and change it if it is wrong.
#: Distinct from the test: this costs nothing and takes a second.
STEP_PREVIEW = "preview"
#: A real run. A call to your own number for a voice agent; a dry run over
#: real data for everything else.
STEP_TEST = "test"
#: Only for an agent that answers a number.
STEP_NUMBER = "number"
#: Adjust the steps and the integrations before going live. Where a standard
#: agent is actually made to fit.
STEP_CUSTOMISE = "customise"
#: What "go live" is called now. You are hiring somebody.
STEP_HIRE = "hire"
#: Only on the build-from-nothing flow: say what it should do, then see what
#: already exists.
STEP_DESCRIBE = "describe"
STEP_SIMILAR = "similar"


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


def flow(pack: AgentPack) -> str:
    """Which hiring flow this role uses."""
    return FLOW_VOICE if is_calling(pack) else FLOW_STANDARD


def _onboarding_step(pack: AgentPack) -> Optional[dict[str, Any]]:
    if not pack.required_facts:
        return None
    return {
        "key": STEP_ONBOARDING,
        "title": "Tell it about your business",
        "detail": "Answered once. Every agent you hire after this already knows.",
        "facts": [fact.model_dump(mode="json") for fact in pack.required_facts],
        "blocking": any(fact.required for fact in pack.required_facts),
    }


def _requirements_step(pack: AgentPack) -> Optional[dict[str, Any]]:
    if not pack.required_connectors:
        return None
    return {
        "key": STEP_REQUIREMENTS,
        "title": "Requirements",
        "detail": "What this role needs access to in order to do the job.",
        "connectors": [
            connector.model_dump(mode="json") for connector in pack.required_connectors
        ],
        "blocking": any(connector.required for connector in pack.required_connectors),
    }


def hire_steps(pack: AgentPack) -> list[dict[str, Any]]:
    """The guided flow for hiring this role, generated from its declaration.

    A step only appears when the role needs it. A role asking for nothing does
    not get an empty form, and one that takes no inbound calls is never asked
    to choose a phone number.
    """
    if is_calling(pack):
        return _voice_steps(pack)
    return _standard_steps(pack)


def _voice_steps(pack: AgentPack) -> list[dict[str, Any]]:
    """Hear it, fit it, hear yours, test it, hire it.

    The listening happens twice and the two are not redundant. The interview is
    the *published* role on our demo number, before any setup, and it is what
    decides whether somebody continues at all. The preview is *their* agent
    with their voice, their preset and their business name, and it is what
    catches a wrong voice or an unreadable Tamil greeting before a real caller
    hears it. Collapsing them would mean either a form before any proof, or a
    customer going live on a voice they never heard.
    """
    steps: list[dict[str, Any]] = [
        {
            "key": STEP_INTERVIEW,
            "title": "Interview it",
            "detail": "Ring the demo number and talk to it before you set anything up.",
            "demo_number": pack.demo_number,
            "blocking": False,
        }
    ]

    onboarding = _onboarding_step(pack)
    if onboarding:
        steps.append(onboarding)

    requirements = _requirements_step(pack)
    if requirements:
        steps.append(requirements)

    # What the customer gets after they hang up. Before the test on purpose:
    # the thing most worth testing is whether the confirmation actually
    # arrived, and it cannot arrive if this has not been set up.
    if pack.after_call_apps:
        steps.append(
            {
                "key": STEP_AFTER_CALL,
                "title": "What happens after the call",
                "detail": (
                    "Send the confirmation, write the record. Connect the "
                    "accounts it should use."
                ),
                "connectors": [
                    connector.model_dump(mode="json")
                    for connector in pack.after_call_apps
                ],
                "blocking": any(
                    connector.required for connector in pack.after_call_apps
                ),
            }
        )

    steps.append(
        {
            "key": STEP_VOICE_AND_BRAIN,
            "title": "Pick its voice and how sharp it is",
            "detail": (
                "Four presets, each with one price a minute. Standard suits "
                "most lines; Smart is for calls that use tools or go off "
                "script."
            ),
            # The chips come from model_presets, which already prices each one
            # and marks the ones we hold no key for. Named here rather than
            # inlined so a preset added there appears here without an edit.
            "presets_from": "model_presets",
            "blocking": False,
        }
    )
    steps.append(
        {
            "key": STEP_PREVIEW,
            "title": "Hear how it sounds",
            "detail": "Its own greeting, in your voice and language. Change it if it is wrong.",
            "blocking": False,
        }
    )
    steps.append(
        {
            "key": STEP_TEST,
            "title": "Test it on a real call",
            "detail": (
                "It rings your phone. Talk to it the way a customer would, "
                "then check the confirmation arrived."
            ),
            "blocking": False,
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

    steps.append(_hire_step())
    return steps


def _standard_steps(pack: AgentPack) -> list[dict[str, Any]]:
    """Fit it, customise it, dry-run it, hire it.

    No interview and no preview: there is nothing to hear. What decides whether
    a back-office agent is any good is whether it read the right rows and wrote
    the right thing, which only the dry run can show.
    """
    steps: list[dict[str, Any]] = []

    onboarding = _onboarding_step(pack)
    if onboarding:
        steps.append(onboarding)

    requirements = _requirements_step(pack)
    if requirements:
        steps.append(requirements)

    steps.append(
        {
            "key": STEP_CUSTOMISE,
            "title": "Make it fit",
            "detail": "Adjust what it does and where it writes, before it touches anything.",
            "blocking": False,
        }
    )
    steps.append(
        {
            "key": STEP_TEST,
            "title": "Dry run it",
            "detail": (
                "It reads your real data and shows you what it would do, "
                "without doing it."
            ),
            "blocking": True,
        }
    )
    steps.append(_hire_step())
    return steps


def _hire_step() -> dict[str, Any]:
    return {
        "key": STEP_HIRE,
        "title": "Hire it",
        "detail": "You can pause it any time, and a paused agent is not billed.",
        "blocking": False,
    }


def blank_flow() -> list[dict[str, Any]]:
    """Hiring nothing: the flow for "none of these fit".

    Starts by asking what the job is and then showing what already exists,
    because most of the time something does -- and a role somebody else has
    hired a hundred times is a better answer than a first draft. Building is
    the fallback, and the three times a week this flow reaches the end are the
    market telling us which role to write next.
    """
    return [
        {
            "key": STEP_DESCRIBE,
            "title": "What should it do?",
            "detail": "In your words. One or two sentences is enough.",
            "blocking": True,
        },
        {
            "key": STEP_SIMILAR,
            "title": "Roles that already do this",
            "detail": "Somebody has probably hired one. Proven beats new.",
            "blocking": False,
        },
        {
            "key": STEP_ONBOARDING,
            "title": "Tell it about your business",
            "detail": "Answered once. Every agent you hire after this already knows.",
            "facts": [],
            "blocking": True,
        },
        {
            "key": STEP_CUSTOMISE,
            "title": "Make it fit",
            "detail": "Adjust the steps and connect what it needs.",
            "blocking": False,
        },
        {
            "key": STEP_TEST,
            "title": "Test it",
            "detail": "Before it touches anything real.",
            "blocking": True,
        },
        _hire_step(),
    ]


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
        "flow": flow(pack),
        "listed": pack.listed,
    }
