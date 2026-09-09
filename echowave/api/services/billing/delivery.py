"""Did the customer actually get the call they are about to be charged for?

A prepaid platform fee is a charge for delivering a conversation. When a
provider fails to *connect*, no conversation is delivered: the agent never
says a word, the caller waits in silence and hangs up, and the run reaches
costing looking like any other completed call. It is then billed one pulse of
platform fee — ₹0.75 at ₹3.00/min — for nothing.

That charge is small and the trust cost is not. It arrives on the invoice of
somebody whose call visibly failed, which is the worst possible line item to
have to explain.

The rule here is deliberately a conjunction, because the two ways of being
wrong are not symmetric:

* charging for silence is the defect this closes;
* waiving a fee we were owed is a revenue leak, and a *silent* one, which is
  worse than the defect if it fires on healthy calls.

So a fee is waived only when something demonstrably broke **and** nothing was
said. A working call is never waived, whatever its shape — in particular a
speech-to-speech realtime model, which may bill audio tokens rather than TTS
characters and so can look "silent" to a naive reader. Requiring a recorded
pipeline error keeps that class of call out of this path entirely.

Provider pass-through is never waived. We paid those vendors for the seconds
they carried, error or not. What is waived is our own margin, which is the
only part that was ours to give up.
"""

from __future__ import annotations

from typing import Any

#: Set by the mute-agent watchdog when it ends a call because the agent never
#: spoke. See services/pipecat/event_handlers.py.
MUTE_AGENT_REASON = "mute_agent"

BOT_TEXT_EVENT = "rtf-bot-text"


def _as_mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def agent_spoke(*, usage_info: Any, logs: Any) -> bool:
    """Positive evidence that the caller heard something from the agent.

    Two independent signals. Absence of both is not by itself enough to waive
    anything — see :func:`platform_fee_is_waived`, which requires a recorded
    failure as well.
    """
    tts = _as_mapping(_as_mapping(usage_info).get("tts"))
    for characters in tts.values():
        if isinstance(characters, (int, float)) and characters > 0:
            return True

    events = _as_mapping(logs).get("realtime_feedback_events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("type") != BOT_TEXT_EVENT:
                continue
            if _as_mapping(event.get("payload")).get("text"):
                return True

    return False


def recorded_failure(*, extra: Any) -> bool:
    """Did this call record a pipeline error of any kind?

    Fatality is not asked. A provider that reported a problem it believed it
    had survived, on a call where the agent then said nothing, did not survive
    it — that combination is exactly the connect failure this exists for.
    """
    error = _as_mapping(_as_mapping(extra).get("pipeline_error"))
    detail = error.get("detail")
    return isinstance(detail, str) and bool(detail.strip())


def platform_fee_is_waived(*, usage_info: Any, logs: Any, extra: Any) -> bool:
    """True when this call failed to deliver anything the fee pays for."""
    if not recorded_failure(extra=extra):
        return False
    return not agent_spoke(usage_info=usage_info, logs=logs)
