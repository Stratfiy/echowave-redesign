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
class Wait(TypedNode):
    """
    Pause the conversation for a moment before continuing.  LLM hint: Holds
    the call for a fixed number of seconds, then continues down its single
    outgoing edge. No LLM turn is taken and the caller is not asked
    anything.  Use it when the flow needs a beat that is not a question:
    after a tool call whose result the caller is waiting on, before
    repeating an important number, or to let a transfer announcement land.
    Do **not** use it to wait for the caller to speak — an agent node
    already waits for a turn, and a wait node on top of that is silence they
    will fill by hanging up.  Say something during anything longer than
    about two seconds. Silence on a phone line reads as a dropped call, and
    `filler_text` is what stops the caller saying 'hello? hello?' into a
    working connection.
    """

    type: ClassVar[str] = 'wait'

    name: str = 'Wait'
    """
    Short identifier shown in the canvas and call logs.
    """

    duration_seconds: float = 2.0
    """
    How long to hold before continuing. Capped at 30 — a longer pause is a
    caller who has already hung up.
    """

    filler_type: Literal['none', 'text', 'audio'] = 'none'
    """
    What the caller hears during the pause.
    """

    filler_text: Optional[str] = None
    """
    Spoken once at the start of the pause. Supports {{template_variables}}.
    """

    filler_recording_id: Optional[str] = None
    """
    Pre-recorded audio played at the start of the pause.
    """

