"""How hard a reasoning model is allowed to think before it answers.

Every OpenAI model whose name contains ``gpt-5`` was being sent
``reasoning_effort="minimal"``, hardcoded, with nothing able to change it. It
arrived inside a large unrelated commit and reads like a latency decision,
which is a reasonable thing to want: a reasoning model that deliberates for
six seconds is unusable on a phone call, and this repository already carries
that scar -- see ``sarvam_llm.py``, where a reasoning model spent an entire
token budget thinking and returned no words at all.

The cost was paid somewhere nobody looked. **Every node transition in a
workflow is a tool call**, and deciding "has the caller told me enough to move
on?" is exactly the judgement that deliberation buys. Measured on the Kriti
Labs agent: on ``gpt-5-mini`` at minimal effort, two of two calls never left
their first node -- the model asked for a truck registration in *words* while
staying in the greeting step, which has no idea how to collect one, so it
asked again, and again. On ``gpt-4.1-mini``, which takes no effort setting and
therefore was never throttled, four of five calls transitioned.

That is a correlation on a small sample, not proof, and it is named here so
the next person can argue with it. What is not in doubt is that the value was
never a decision anyone made per agent, and that the failure it produces is
invisible: the agent keeps talking, so nothing errors, and only a transcript
shows the conversation going in circles.

So the setting becomes a setting. The default moves from ``minimal`` to
``low`` -- the smallest step that leaves room to choose a tool -- and an
operator who wants the old behaviour, or wants to spend more, can say so.
``minimal`` is still offered and still honest about what it costs.
"""

from __future__ import annotations

from typing import Any

#: What OpenAI accepts, cheapest first.
LEVELS: tuple[str, ...] = ("minimal", "low", "medium", "high")

#: The default for an agent that names none.
#:
#: Not ``minimal``. That was the old hardcoded value and it is the one
#: measured failing to emit node transitions. Not ``medium`` either: this runs
#: on a phone call, and the whole reason a throttle existed is that thinking
#: time is dead air. ``low`` is the cheapest level that still deliberates.
DEFAULT = "low"


#: Only these models take the parameter at all. Sending it to a model that
#: does not understand it is a 400 on the first turn of a live call.
def takes_reasoning_effort(model: str | None) -> bool:
    """Does this model accept a reasoning-effort setting?

    Matched on the model name, the same way the factory already decides to
    omit ``temperature`` for these models. A name-based test is crude, and it
    is what the vendor's own naming makes available.
    """
    return "gpt-5" in (model or "")


def resolve(raw: Any) -> str:
    """The configured effort, or the default for anything unrecognised.

    Deliberately forgiving: this sits on the path that builds a live call, and
    a typo in a stored configuration should cost the agent its preferred
    setting, not its ability to answer the phone.
    """
    if not isinstance(raw, str):
        return DEFAULT
    cleaned = raw.strip().lower()
    return cleaned if cleaned in LEVELS else DEFAULT
