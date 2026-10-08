""" "Call it for me": Decibyl places one phone call on a person's behalf.

The flow, and the rules it keeps:

1. The person asks ("call the salon and book a haircut for Saturday").
   Decibyl's ``call_for_me`` tool proposes a ``place_call`` card -- it never
   dials. The card says exactly who will be called, about what, what may be
   shared, and that it cannot be undone (actions.py; handoff 10).
2. The person approves that exact version. Edits are a new version and need
   a new approval (task ledger).
3. The card runs once (actions.py compare-and-swap). Before dialling: the
   phone line and the Call and Appointment helper must be set up, the number
   must not be on the workspace's do-not-disturb list and the calling window
   must be open (``dnd.assert_may_call``) -- every guard ``dial_workflow``
   does not hold itself, in ``missed_call.place_callback``'s order.
4. The call opens with an announcement the helper cannot leave out: "Hello,
   this is Decibyl, an AI assistant calling on behalf of <name>." It rides on
   the run's context as ``on_behalf_announcement``, which the engine speaks
   before any greeting even where the agent's own AI line is switched off
   (``PipecatEngine.resolve_ai_disclosure``).
5. A provider error midway is ``outcome_unknown``, never a blind redial.

Off (``call_for_me``), the tool is not offered and a stored card refuses to
run.
"""

from __future__ import annotations

from typing import Any

from api.services import features
from api.services.compliance import dnd

FLAG = "call_for_me"
TOOL_NAME = "call_for_me"
#: The run-context key the engine speaks first on an on-behalf call.
ANNOUNCEMENT_KEY = "on_behalf_announcement"
MAX_PURPOSE = 300
MAX_DETAILS = 500

RULES = (
    "- call_for_me places ONE phone call for the person who asked, after "
    "they approve the card it shows. Use it only when they ask you to call "
    "somebody for them. Ask for the number and what the call should achieve "
    "if you do not have them. Put in `details` only what they said may be "
    "shared. Never say the call was made: say the card is waiting for "
    "their approval. The call always opens by saying it is Decibyl, an AI "
    "assistant calling on their behalf.\n"
)


class CallNotPossible(ValueError):
    """Said to the model or on the card: why this call cannot be proposed or
    placed. Never retried."""


def enabled(organization_id: int | None) -> bool:
    return features.is_on(FLAG, organization_id)


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Propose one phone call made for the person who asked: a card "
            "they approve before anything is dialled."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "phone_number": {
                    "type": "string",
                    "description": "The number to call, as the person gave it.",
                },
                "callee": {
                    "type": "string",
                    "description": "Who is being called, e.g. 'Green Trends salon'.",
                },
                "purpose": {
                    "type": "string",
                    "description": (
                        "What the call should achieve, in one sentence, e.g. "
                        "'Book a haircut for Saturday afternoon'."
                    ),
                },
                "details": {
                    "type": "string",
                    "description": (
                        "Only what the person said may be shared on the call, "
                        "e.g. their first name or a booking reference."
                    ),
                },
                "why": {"type": "string", "description": "One line on why."},
            },
            "required": ["phone_number", "purpose", "why"],
        },
    }


def announcement(principal_name: str | None) -> str:
    """The first words of every on-behalf call."""
    who = (principal_name or "").strip() or "a Decibyl user"
    return f"Hello, this is Decibyl, an AI assistant calling on behalf of {who}."


def masked(number: str) -> str:
    """+91 98••••3210: enough to recognise, not enough to copy."""
    digits = number.lstrip("+")
    if len(digits) < 8:
        return number
    return f"+{digits[:-10] or ''} {digits[-10:-8]}••••{digits[-4:]}".replace("+ ", "+")


def _text(arguments: dict[str, Any], key: str, limit: int) -> str:
    value = str(arguments.get(key) or "").strip()
    if len(value) > limit:
        raise CallNotPossible(
            f"The {key} is too long; keep it under {limit} characters."
        )
    return value


async def _principal(user_id: int | None) -> tuple[int, str]:
    """The person the call is for, and the name they asked to be called by
    (screen 02's preferred name). No name is said as "a Decibyl user" --
    the card shows the exact words before anyone approves them."""
    from api.db import db_client
    from api.db.shell_models import UserOnboardingModel

    if not user_id:
        raise CallNotPossible("Only a signed-in person can ask for a call.")
    user = await db_client.get_user_by_id(user_id)
    if user is None:
        raise CallNotPossible("Only a signed-in person can ask for a call.")
    async with db_client.async_session() as session:
        onboarding = await session.get(UserOnboardingModel, user.id)
    name = (getattr(onboarding, "preferred_name", None) or "").strip()
    return user.id, name


async def resolve(
    *, organization_id: int, arguments: dict[str, Any], user_id: int | None
) -> dict[str, Any]:
    """The card's payload. Raises CallNotPossible with words for the model."""
    from api.services.voice import readiness

    if not enabled(organization_id):
        raise CallNotPossible("Calls on your behalf are switched off.")
    state = await readiness.calls(organization_id=organization_id)
    if state.state != readiness.AVAILABLE:
        raise CallNotPossible(
            " ".join(p for p in (state.reason, state.next_step) if p)
            or "Calls are not set up."
        )
    normalised = dnd.normalise_number(arguments.get("phone_number"))
    if normalised is None:
        raise CallNotPossible(
            "That does not look like a phone number. Ask for it again."
        )
    number = dnd.to_dialable(normalised)
    purpose = _text(arguments, "purpose", MAX_PURPOSE)
    if not purpose:
        raise CallNotPossible("Say what the call should achieve before proposing it.")
    callee = _text(arguments, "callee", 120)
    details = _text(arguments, "details", MAX_DETAILS)
    principal_id, principal_name = await _principal(user_id)
    who = callee or masked(number)
    return {
        "args": {
            "to": number,
            "callee": callee,
            "purpose": purpose,
            "details": details,
            "principal_user_id": principal_id,
        },
        "label": f"Call {who} for you: {purpose}"[:200],
        # A call made for one person is theirs alone to approve or stop:
        # to anyone else in the workspace the card is not there
        # (actions.answer_refusal).
        "private_to": principal_id,
        "reversible": False,
        "reaches_people": True,
        "effect": (
            f"Calls {masked(number)} now and speaks for you. It opens with: "
            f"“{announcement(principal_name)}” It cannot be undone."
        ),
    }


async def execute(
    *, organization_id: int, payload: dict[str, Any], event_id: int
) -> str:
    """Place the approved call. Returns the line under the card.

    Raises CallNotPossible for every refusal that dialled nothing (the card
    fails with the reason); anything else propagates and actions.py marks
    the card outcome unknown, never redialled.
    """
    from api.db import db_client
    from api.services.organization_preferences import get_organization_preferences
    from api.services.telephony import outbound
    from api.services.telephony.factory import get_default_telephony_provider
    from api.services.voice import appointments, readiness

    if not enabled(organization_id):
        raise CallNotPossible("Calls on your behalf were switched off before this ran.")
    state = await readiness.calls(organization_id=organization_id)
    if state.state != readiness.AVAILABLE:
        raise CallNotPossible(state.reason or "Calls are not set up.")
    args = payload.get("args") or {}
    number = str(args.get("to") or "")
    policy = await appointments.get_policy(organization_id)
    workflow = await db_client.get_workflow(
        policy["call_workflow_id"], organization_id=organization_id
    )
    if workflow is None:
        raise CallNotPossible("The Call and Appointment helper no longer exists.")
    preferences = await get_organization_preferences(organization_id, db=db_client)
    try:
        dialable = await dnd.assert_may_call(
            organization_id,
            number,
            timezone_name=preferences.timezone,
            db=db_client,
        )
    except dnd.CallRefused as exc:
        raise CallNotPossible(str(exc)) from exc
    telephony = await db_client.get_default_telephony_configuration(organization_id)
    provider = await get_default_telephony_provider(organization_id)
    principal_id, principal_name = await _principal(args.get("principal_user_id"))
    version = payload.get("version") or ""
    from api.services.people import interactions as people_interactions
    from api.services.people import tools as people_tools

    # People: what the person keeps about whoever is called, only when they
    # let agents read it (People settings). None otherwise, and the key is
    # then empty.
    callee_brief = await people_tools.brief_for_agent(
        organization_id, principal_id, phone=dialable
    )
    try:
        run_id = await outbound.dial_workflow(
            workflow=workflow,
            organization_id=organization_id,
            to_number=dialable,
            provider=provider,
            telephony_configuration_id=getattr(telephony, "id", None),
            source="call_for_me",
            extra_context={
                "trigger_source": "call_for_me",
                ANNOUNCEMENT_KEY: announcement(principal_name),
                "principal_name": principal_name,
                # Who to tell when the call ends (mobile_push.announce_call).
                "principal_user_id": args.get("principal_user_id"),
                "callee_name": args.get("callee") or "",
                "call_purpose": args.get("purpose") or "",
                "call_details": args.get("details") or "",
                "card_event_id": event_id,
                "idempotency_key": f"card:{event_id}:{version}",
                # Whose call this is, for the record after it ends (People).
                "principal_user_id": principal_id,
                "callee_brief": callee_brief or "",
            },
        )
    except (
        outbound.OutboundRefused,
        outbound.NoConcurrencySlot,
        outbound.QuotaExhausted,
    ) as exc:
        raise CallNotPossible(str(exc) or "The call could not be placed.") from exc
    payload["result"] = {"workflow_run_id": run_id}
    await people_interactions.record(
        organization_id,
        principal_id,
        channel="call",
        direction="out",
        phone=dialable,
        name=args.get("callee") or None,
        line=f"Decibyl called for you: {args.get('purpose') or 'a call'}",
        ref=f"run:{run_id}",
    )
    who = args.get("callee") or masked(dialable)
    return f"Calling {who} now. The call opens by saying it is Decibyl calling for you."
