"""GENERATED — do not edit by hand.

Regenerate with `python -m decibyl_sdk.codegen` against the target
Decibyl backend. Source of truth: the backend's model-backed node-spec
catalog served from `/api/v1/node-types`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Optional

from decibyl_sdk.typed._base import TypedNode


@dataclass(kw_only=True)
class Sms(TypedNode):
    """
    Text the caller after the call — SMS or WhatsApp, on the carrier you
    already use.  LLM hint: Sends one SMS or WhatsApp message after the call
    finishes, on the organisation's existing telephony credentials. No new
    account is needed: Twilio and Plivo bill messaging to the same account
    as voice.  Like the webhook node it is **detached** — no edges in or
    out. It is not a step in the conversation; it fires once the run
    completes, with the run's variables available to the template.  Use it
    for the artefact the caller keeps: a confirmation, a reference number, a
    payment or application link. `to` defaults to the number that was on the
    call, which is what you want almost every time.  Only send when it is
    worth sending. The `send_when_*` fields take the same
    variable/operator/value shape as a branch rule, so a failed call does
    not text somebody a confirmation of nothing.
    """

    type: ClassVar[str] = 'sms'

    name: str = 'Send Message'
    """
    Short identifier shown in the canvas and call logs.
    """

    enabled: bool = True
    """
    When false, nothing is sent.
    """

    channel: Literal['sms', 'whatsapp'] = 'sms'
    """
    SMS goes over the telephony configuration's carrier. WhatsApp requires a
    Twilio WhatsApp Business sender.
    """

    to: Optional[str] = None
    """
    Recipient in E.164 (+919876543210). Leave blank to use the number that
    was on the call. Supports {{template_variables}}.
    """

    from_number: Optional[str] = None
    """
    Sender number. Leave blank to use the configuration's default outbound
    caller ID.
    """

    body: str = ''
    """
    Supports {{template_variables}} from the call — extracted values,
    campaign columns, trigger payload.
    """

    send_when_variable: Optional[str] = None
    """
    Leave blank to always send. Same shape as a branch rule.
    """

    send_when_operator: Optional[Literal['equals', 'not_equals', 'contains', 'in_list', 'greater_than', 'less_than', 'is_true', 'is_not_empty']] = None
    """
    How the variable is compared.
    """

    send_when_value: Optional[str] = None
    """
    Blank for 'is true' and 'is not empty'.
    """

