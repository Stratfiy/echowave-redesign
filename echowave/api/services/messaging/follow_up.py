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


async def deliver(
    node_data,
    *,
    variables: Mapping[str, Any],
    provider: str,
    credentials: Mapping[str, Any],
    default_from: str = "",
) -> SendResult | None:
    """Send the node's message if its condition holds.

    Returns ``None`` when nothing was sent by design — a disabled node or a
    condition that did not hold — so the caller can tell "we chose not to" from
    "we tried and failed".
    """
    variables = dict(variables)

    ok, reason = should_send(node_data, variables)
    if not ok:
        logger.info(
            "Send Message '{}' skipped: {}.",
            getattr(node_data, "name", "message"),
            reason,
        )
        return None

    to = resolve_recipient(node_data, variables)
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
        return await send_message(
            provider=effective_provider,
            credentials=credentials,
            to=to,
            from_=from_,
            body=body,
            template=resolve_template(node_data, variables)
            if effective_provider == META_WHATSAPP
            else None,
        )
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
