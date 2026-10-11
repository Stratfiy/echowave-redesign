"""Turn a Send Message node into an actual message, after the call.

Sits between the post-call task and :mod:`api.services.messaging.send`: works
out whether to send, who to, from what, and with what text, then hands the
result back so it can be written onto the run.

**Nothing here raises into the task.** A follow-up message is the least
important thing happening on this path — the conversation is over, the outcome
is recorded, the receipt is costed — and taking the post-call pipeline down
because a carrier returned 429 would trade all of that for an SMS.
"""

from __future__ import annotations

from typing import Any, Mapping

from loguru import logger

from api.services.messaging.send import (
    META_WHATSAPP,
    MessagingError,
    SendResult,
    send_message,
)
from api.services.workflow.branching import Rule, evaluate_rule
from api.utils.template_renderer import render_template

#: Where to look for the number that was on the call, in order. Runs arrive from
#: campaigns, from the public trigger API and from inbound calls, and each names
#: the other party differently — the node should not have to know which.
CALLER_NUMBER_KEYS: tuple[str, ...] = (
    "phone_number",
    "to_phone_number",
    "caller_number",
    "from_number",
    "contact_number",
    "mobile",
)


def should_send(node_data, variables: Mapping[str, Any]) -> tuple[bool, str]:
    """Whether the node's condition holds. No condition means always."""
    if not getattr(node_data, "enabled", True):
        return False, "node is disabled"

    variable = (getattr(node_data, "send_when_variable", None) or "").strip()
    if not variable:
        return True, "no condition"

    matched, reason = evaluate_rule(
        Rule(
            label="send",
            variable=variable,
            operator=(getattr(node_data, "send_when_operator", None) or "").strip(),
            value=getattr(node_data, "send_when_value", None) or "",
        ),
        variables,
    )
    return matched, reason


def resolve_recipient(node_data, variables: Mapping[str, Any]) -> str:
    """The number to text: the node's, or the one that was on the call.

    Defaulting to the call's own number is the behaviour that is right almost
    every time — you are texting the person you just spoke to — and it saves
    every author from knowing which key their trigger used.
    """
    explicit = (getattr(node_data, "to", None) or "").strip()
    if explicit:
        return render_template(explicit, dict(variables))

    # The number arrives at the top level from a campaign row or a trigger,
    # and nested under the run's own contexts on a phone call — the post-call
    # render context keeps ``initial_context`` and ``gathered_context`` as
    # sections. Both are searched, or a real call texts nobody.
    sections: list[Mapping[str, Any]] = [variables]
    for section in ("gathered_context", "initial_context"):
        nested = variables.get(section)
        if isinstance(nested, Mapping):
            sections.append(nested)
    for candidate in sections:
        for key in CALLER_NUMBER_KEYS:
            value = candidate.get(key)
            if value and str(value).strip():
                return str(value).strip()
    return ""


def resolve_template(node_data, variables: Mapping[str, Any]) -> dict[str, Any] | None:
    """The approved WhatsApp template the node names, with its values filled.

    ``template_params`` is a comma-separated list, each entry rendered from
    the call's variables, in the order of the template's placeholders.
    """
    name = (getattr(node_data, "template_name", None) or "").strip()
    if not name:
        return None
    raw = getattr(node_data, "template_params", None) or ""
    params = [
        render_template(part.strip(), dict(variables))
        for part in raw.split(",")
        if part.strip()
    ]
    return {
        "name": name,
        "language": (getattr(node_data, "template_language", None) or "en").strip(),
        "params": params,
    }


#: How a call ended when nobody had a conversation, in the words the
#: disposition step uses. Matched as words inside the disposition.
NOT_A_CONVERSATION = ("voicemail", "no_answer", "no answer", "busy", "failed")


def real_conversation(run: Any) -> bool:
    """Whether somebody actually spoke with the agent on this run: a chat
    that happened, or a call that was answered and did not end on a
    voicemail, a busy line or nobody. A test run never counts -- an owner
    trying their agent is not a customer to message."""
    from api.services.workflow import answered as answered_rule
    from api.services.workflow import test_runs

    if test_runs.is_test(run):
        return False
    gathered = getattr(run, "gathered_context", None) or {}
    disposition = str(
        gathered.get("mapped_call_disposition")
        or gathered.get("call_disposition")
        or ""
    ).lower()
    if any(word in disposition for word in NOT_A_CONVERSATION):
        return False
    if (getattr(run, "mode", "") or "") == "textchat":
        return True
    return answered_rule.was_answered(run)


#: Where "sent this person this message already" is remembered, per
#: workspace, agent, message and number, for ``once_per_contact_days``.
ONCE_KEY = "follow_up_once:{organization_id}:{workflow_id}:{node}:{to}"


def _once_key(
    node_data, *, organization_id: int | None, workflow_id: int | None, to: str
) -> str:
    digits = "".join(ch for ch in to if ch.isdigit())
    node = str(getattr(node_data, "name", "") or "message").strip().lower()
    return ONCE_KEY.format(
        organization_id=organization_id or 0,
        workflow_id=workflow_id or 0,
        node=node,
        to=digits or to,
    )


async def _redis():
    import redis.asyncio as aioredis

    from api.constants import REDIS_URL

    return aioredis.from_url(REDIS_URL, decode_responses=True)


async def _sent_recently(key: str) -> bool:
    """Whether the key is held. Unknown (Redis down) reads as not sent: a
    second message is a nuisance somebody notices; a message that silently
    never goes is not (AGENTS.md, Silent Absence)."""
    try:
        client = await _redis()
        try:
            return bool(await client.exists(key))
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not check {} before sending: {}", key, exc)
        return False


async def _remember_sent(key: str, days: int) -> None:
    try:
        client = await _redis()
        try:
            await client.set(key, "1", ex=int(days) * 24 * 60 * 60)
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not remember {} was sent: {}", key, exc)


async def deliver(
    node_data,
    *,
    variables: Mapping[str, Any],
    provider: str,
    credentials: Mapping[str, Any],
    default_from: str = "",
    answered: bool | None = None,
    organization_id: int | None = None,
    workflow_id: int | None = None,
) -> SendResult | None:
    """Send the node's message if its condition holds.

    Returns ``None`` when nothing was sent by design — a disabled node, a
    condition that did not hold, no real conversation for a node that asks
    for one (``answered`` is the caller's word on it), or a person already
    sent this message within ``once_per_contact_days`` — so the caller can
    tell "we chose not to" from "we tried and failed".
    """
    variables = dict(variables)
    name = getattr(node_data, "name", "message")

    ok, reason = should_send(node_data, variables)
    if not ok:
        logger.info("Send Message '{}' skipped: {}.", name, reason)
        return None

    if getattr(node_data, "only_if_answered", False) and answered is not True:
        logger.info(
            "Send Message '{}' skipped: nobody was on the line (or a test).", name
        )
        return None

    to = resolve_recipient(node_data, variables)
    days = getattr(node_data, "once_per_contact_days", None)
    once_key = (
        _once_key(
            node_data, organization_id=organization_id, workflow_id=workflow_id, to=to
        )
        if days and to
        else None
    )
    if once_key and await _sent_recently(once_key):
        logger.info(
            "Send Message '{}' skipped: this person had it within {} days.",
            name,
            days,
        )
        return None
    from_ = (
        render_template(
            (getattr(node_data, "from_number", None) or "").strip(), variables
        )
        or default_from
    )
    body = render_template(getattr(node_data, "body", "") or "", variables)

    # A WhatsApp step goes out on the platform sender when the caller hands
    # one in (provider ``meta_whatsapp``); otherwise WhatsApp is Twilio's
    # Business API, so the channel picks the transport while the carrier
    # configuration still supplies the credentials.
    channel = (getattr(node_data, "channel", "sms") or "sms").strip().lower()
    if provider == META_WHATSAPP:
        effective_provider = META_WHATSAPP
    else:
        effective_provider = "whatsapp" if channel == "whatsapp" else provider

    try:
        result = await send_message(
            provider=effective_provider,
            credentials=credentials,
            to=to,
            from_=from_,
            body=body,
            template=resolve_template(node_data, variables)
            if effective_provider == META_WHATSAPP
            else None,
        )
        if once_key and result.ok:
            await _remember_sent(once_key, int(days))
        return result
    except MessagingError as exc:
        # A configuration mistake — unsupported carrier, missing credential,
        # unusable number. Recorded like any other failure rather than raised,
        # because the run's own data is worth more than this message.
        logger.warning(
            "Send Message '{}' could not be sent: {}",
            getattr(node_data, "name", "message"),
            exc,
        )
        return SendResult(ok=False, provider=effective_provider, to=to, error=str(exc))
