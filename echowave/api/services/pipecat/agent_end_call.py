"""Let the agent hang up, when the operator has said it may.

Ending a call has been the caller's job alone. ``end_call_phrases`` watches
their words for "okay bye" and hangs up without a model turn, which is right
for a caller who has finished and is already lowering the phone.

It cannot help with the other ending. A tester rang, said "No, I am not
there", and the agent answered "Okay, I will close the call" -- and then kept
talking for another twenty-four seconds, because saying it was the only thing
it could do. There was no hang-up it could reach. The words were a promise the
runtime had no way to keep.

So this is the tool the model was missing. It is a function like any other on
the node: the model calls it, the runtime says the farewell and ends the call.
What the model cannot do is end a call by *describing* one.

**It is off unless switched on, per agent.** An agent that can hang up will,
sometimes wrongly, and an operator who has not asked for that would rather a
caller sat through a confused turn than be cut off mid-sentence. The same
posture the near-speaker gate takes, for the same reason.

**The reason is recorded, not the model's wording.** ``agent_ended_call``
lands in the call disposition so a run can be found and listened to later. A
free-text reason from the model would be a new vocabulary on every call and
useless to group by, so the argument is a short enumerated one.
"""

from __future__ import annotations

from typing import Any, Mapping

#: The per-agent key in ``workflow_configurations``.
CONFIG_KEY = "agent_can_end_call"

#: The function the model calls. Named for what it does to the call rather
#: than for the conversation reaching its end, because the model has to pick
#: it over a node transition and the difference has to be obvious.
TOOL_NAME = "end_call"

#: Why the agent hung up. Enumerated so the dispositions group; a free-text
#: reason would be a fresh phrase every call.
REASONS: tuple[str, ...] = (
    "caller_done",
    "caller_absent",
    "caller_abusive",
    "wrong_number",
    "cannot_help",
)

DEFAULT_REASON = "caller_done"

#: What the call is tagged with afterwards.
DISPOSITION = "agent_ended_call"

DESCRIPTION = (
    "End this call now. Call this only when there is nothing left to do: the "
    "caller has finished and said so, nobody is on the line, the caller is "
    "abusive, they reached the wrong number, or you have told them you cannot "
    "help and they have acknowledged it. Never call it to avoid a difficult "
    "question, and never call it in the same turn as promising to do something "
    "for the caller. Saying you will end the call does not end it; this does."
)


def wants_agent_end_call(run_configs: Any) -> bool:
    """May this agent hang up on its own? Off unless explicitly switched on.

    ``True`` only for a real boolean. A stored ``"true"`` string is a
    configuration bug rather than consent, and the failure it causes -- an
    agent cutting callers off that nobody asked to be able to -- is worse
    than the one it prevents.
    """
    if not isinstance(run_configs, Mapping):
        return False
    return run_configs.get(CONFIG_KEY) is True


def resolve_reason(raw: Any) -> str:
    """The model's stated reason, if it is one we recorded, else the default."""
    if not isinstance(raw, str):
        return DEFAULT_REASON
    cleaned = raw.strip().lower()
    return cleaned if cleaned in REASONS else DEFAULT_REASON


def tool_properties() -> dict[str, Any]:
    return {
        "reason": {
            "type": "string",
            "enum": list(REASONS),
            "description": "Why the call is being ended.",
        }
    }
