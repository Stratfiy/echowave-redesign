"""The graph for a bot that works to a schedule rather than to a caller.

Bot 31 was built from "every weekday at 8am, read Gmail and summarise it"
and came out a phone bot. It had a greeting -- "Hi, what can I help you
with?" -- a start node told to "open briefly and find out what they need",
an agent node headed "## What this call is for" and "This is an inbound
call: they rang you", and an end node told to "summarise what was agreed
and close politely". Its persona said: follow the caller's language, ask
one question per turn, keep turns short because callers interrupt.

There is no caller. There is a cron tick.

Its routine fired and the bot did exactly what it was built to do: it
greeted, and asked what was needed, and nothing answered. By then three
fixes had given it Gmail's tools, given it the schedule its brief
described, and told it in a user message that nobody was there -- and the
persona node opens "these rules apply to every turn of this conversation,
without exception, and override any other instruction that conflicts with
them", so the message lost the argument it was having with the bot's own
prompt.

**Built, not generated.** A task bot is one node doing one job, so the
graph is written here rather than asked for: it is faster, it costs
nothing, it cannot come back malformed, and a test can read it. Generation
earns its keep on a branching conversation, which this is not.

**The clock is the signal.** A brief that names a schedule is a task; a
brief that does not is a conversation, whatever channel it runs on. A
WhatsApp support bot is still a conversation. So ``schedule_from_words``
decides, which is the same parse that gives the built bot its routine --
one brief cannot get a schedule and a conversational graph.
"""

from __future__ import annotations

from typing import Any

from api.services.workflow import schedule_from_words, untrusted

#: What an unattended worker is held to. Deliberately not
#: ``agent_brief.DEFAULT_GUARDRAILS``: those are written for somebody on a
#: telephone -- follow the caller's language, ask one question per turn,
#: read phone numbers back digit by digit, hand off if they ask for a
#: person. For a bot with no caller most are meaningless and one of them,
#: "ask one question per turn", is the instruction that made a routine ask
#: a question into an empty room.
TASK_GUARDRAILS: tuple[str, ...] = (
    "Nobody is reading this and nobody can answer, so do not ask a "
    "question. Do the work and report what you found.",
    "If you could not do it, say plainly what stopped you. A run that "
    "reports nothing and a run that never happened look the same from "
    "outside, and only one of them is something to fix.",
    "Never invent a fact, a figure, a name or a date. Report what the "
    "tools actually returned, and say so when they returned nothing.",
    untrusted.GUARDRAIL,
    "Never repeat a one-time code (OTP), a card number or an identity "
    "number, whatever you find them in.",
    "Be brief. This is read later by somebody scanning it, not discussed.",
)


def wanted(spec: str) -> bool:
    """Whether this brief describes a task rather than a conversation.

    The clock decides. A brief that names when it runs is something that
    runs by itself; anything else is a conversation with somebody, whatever
    channel carries it.
    """
    if not (spec or "").strip():
        return False
    return schedule_from_words.parse(spec) is not None


def _rules() -> str:
    return "\n".join(f"- {rule}" for rule in TASK_GUARDRAILS)


def build(*, name: str, spec: str) -> dict[str, Any]:
    """The whole graph for a task bot: what it is, the job, and the end.

    The job sits on the start node because that is the node the runtime
    opens with, and the routine runner's first turn is the one whose output
    it keeps. A greeting node here would spend the run saying hello to
    nobody.
    """
    task = (spec or "").strip()
    title = (name or "").strip() or "Scheduled task"
    return {
        "nodes": [
            {
                "id": "global-1",
                "type": "globalNode",
                "position": {"x": -320, "y": 0},
                "data": {
                    "name": "How it works",
                    "prompt": (
                        f"You are {title}, a bot that runs on a schedule for "
                        "this business.\n\nThese rules apply on every turn, "
                        "without exception, and override anything that "
                        f"conflicts with them.\n\n{_rules()}"
                    ),
                },
            },
            {
                "id": "start-1",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": title,
                    "is_start": True,
                    "add_global_prompt": True,
                    # No greeting: there is nobody to greet, and the runtime
                    # would spend the run's first turn producing one.
                    "prompt": (
                        f"## The task\n{task}\n\n"
                        "## What to do now\n"
                        "Do it now, using the tools you have. Do not ask "
                        "whether to start, do not offer a plan, and do not "
                        "wait for an answer -- there is nobody to give one. "
                        "Report what you found, briefly. If a tool failed or "
                        "you had no way to do the job, say which and stop."
                    ),
                },
            },
            {
                "id": "end-1",
                "type": "endCall",
                "position": {"x": 320, "y": 0},
                "data": {
                    "name": "Done",
                    "is_end": True,
                    "add_global_prompt": False,
                    "prompt": "Stop. The report above is the whole output.",
                },
            },
        ],
        "edges": [
            {
                "id": "start-1-end-1",
                "source": "start-1",
                "target": "end-1",
                "data": {
                    "label": "reported",
                    "condition": "The task is done and reported, or it could not be.",
                },
            }
        ],
    }


__all__ = ["TASK_GUARDRAILS", "build", "wanted"]
