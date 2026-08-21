"""The structured brief a person fills in to create an agent, and what we do with it.

The create screen used to ask three questions: inbound or outbound, a use-case
label, and a free-text description. That is a reasonable prompt and a poor
form. It asks somebody who has never built a voice agent to know, unaided, that
the description should mention the persona, the opening line, what to do when
the caller is busy, and what never to say — and when they do not, the generated
agent is missing exactly those things.

So the brief is structured instead. Same information, asked for in the order
somebody actually knows it, with the parts that are legally or operationally
load-bearing given their own fields rather than left to be remembered.

Two things happen with it, and the split matters:

**The prose parts are composed into a description and sent to generation.**
Persona, industry, objective and flow are guidance — a language model is the
right instrument for turning them into a graph of prompts.

**The guardrails and the closing line are applied afterwards, in code.**
A guardrail asked for in a prompt is a guardrail the model may or may not have
honoured, and there is no way to tell by looking at the result. Hoisted into a
global node here, they apply on every turn of every call because the runtime
puts them there, not because a generation step remembered to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Applied to every agent unless the operator removes them, because each one is
#: something an Indian voice deployment gets wrong by default and is judged on.
#: The first four are how a caller decides whether the thing is worth talking
#: to; the last four are how a regulator decides whether it should have called.
DEFAULT_GUARDRAILS: tuple[str, ...] = (
    "Follow the caller's language. If they answer in Hindi, Telugu, Tamil or "
    "any other language, switch to it immediately and stay there. Never insist "
    "on English.",
    "Ask one question per turn. Never stack two questions in a single turn.",
    "Keep every turn under about three sentences. Callers interrupt long turns "
    "and everything after the interruption is lost.",
    "Read back phone numbers digit by digit, and read back dates, times and "
    "amounts, before treating them as confirmed.",
    "Never invent a fact, a price, a date or a commitment. If you do not have "
    "the information, say so plainly and offer to have a person follow up.",
    "Never claim to be a human being if you are asked directly.",
    "If the caller asks to speak to a person, stop the flow and hand off "
    "immediately. Do not attempt to resolve their issue first.",
    "If the caller says they are busy, ask once for a better time, then end "
    "the call politely.",
    "If the caller asks not to be contacted again, confirm that you have noted "
    "it, apologise once, and end the call. Do not attempt to continue.",
)

TONES: tuple[str, ...] = (
    "Warm & professional",
    "Formal",
    "Casual",
    "Assertive",
    "Empathetic",
)


@dataclass(frozen=True)
class AgentBrief:
    """What the create wizard collected. Every field past the first is optional.

    Optional because the wizard is a better form than the old one, not a longer
    one: somebody who fills in only the objective should still get an agent,
    and each additional answer should make it measurably better rather than
    being the price of admission.
    """

    call_type: str
    use_case: str = ""
    objective: str = ""

    agent_name: str = ""
    agent_designation: str = ""
    company_name: str = ""
    company_description: str = ""
    industry: str = ""
    languages: list[str] = field(default_factory=list)
    gender: str = ""
    tone: str = ""

    welcome_message: str = ""
    conversation_flow: str = ""
    guardrails: list[str] = field(default_factory=list)

    closing_line: str = ""
    hangup_prompt: str = ""

    @property
    def is_outbound(self) -> bool:
        return (self.call_type or "").strip().upper() == "OUTBOUND"

    def effective_guardrails(self) -> list[str]:
        """The operator's guardrails, or ours when they have not written any.

        Not merged. An operator who edited the list has made a decision about
        it, and silently re-adding a rule they deleted would be the kind of
        surprise that makes a control untrustworthy.
        """
        written = [rule.strip() for rule in self.guardrails if rule and rule.strip()]
        return written or list(DEFAULT_GUARDRAILS)


def _section(title: str, body: str) -> str:
    return f"## {title}\n{body.strip()}" if body and body.strip() else ""


def compose_activity_description(brief: AgentBrief) -> str:
    """Turn the structured brief into the prose generation actually reads.

    Written as headed sections rather than a paragraph because generation is
    being asked to produce a graph, and a brief with visible structure produces
    a graph with visible structure. The guardrails are included here as well as
    being enforced later — telling the model about them makes it write prompts
    that do not fight them, and enforcing them separately makes it not matter
    whether it did.
    """
    who = []
    if brief.agent_name:
        who.append(f"You are {brief.agent_name}")
        if brief.agent_designation:
            who[-1] += f", {brief.agent_designation}"
    if brief.company_name:
        who.append(f"You are calling on behalf of {brief.company_name}")
        if brief.company_description:
            who[-1] += f", which {brief.company_description.rstrip('.')}"
    if brief.tone:
        who.append(f"Your manner is {brief.tone.lower()}")
    if brief.languages:
        who.append(f"You speak {', '.join(brief.languages)}")

    direction = (
        "This is an outbound call: you are ringing them."
        if brief.is_outbound
        else "This is an inbound call: they rang you."
    )

    guardrails = "\n".join(f"- {rule}" for rule in brief.effective_guardrails())

    sections = [
        _section("Who you are", ". ".join(who) + "." if who else ""),
        _section("Industry", brief.industry),
        _section("What this call is for", brief.objective),
        _section("Direction", direction),
        _section("How the conversation should go", brief.conversation_flow),
        _section("Rules that always apply", guardrails),
        _section("How the call should end", brief.hangup_prompt),
    ]
    return "\n\n".join(section for section in sections if section)


def _global_prompt(brief: AgentBrief) -> str:
    """The guardrails, as an instruction that applies on every turn."""
    rules = "\n".join(f"- {rule}" for rule in brief.effective_guardrails())
    persona = []
    if brief.agent_name:
        line = f"You are {brief.agent_name}"
        if brief.agent_designation:
            line += f", {brief.agent_designation}"
        if brief.company_name:
            line += f" at {brief.company_name}"
        persona.append(line + ".")
    if brief.tone:
        persona.append(f"Your manner is {brief.tone.lower()}.")
    if brief.languages:
        persona.append(
            f"You speak {', '.join(brief.languages)}. "
            "Answer in whichever of them the caller uses."
        )

    head = " ".join(persona)
    return (f"{head}\n\n" if head else "") + (
        "These rules apply to every turn of this conversation, without "
        "exception, and override any other instruction that conflicts with "
        f"them.\n\n{rules}"
    )


def apply_brief(definition: dict[str, Any], brief: AgentBrief) -> dict[str, Any]:
    """Put the parts that must be exact into the generated graph, exactly.

    Generation is good at structure and unreliable about verbatim text, so
    anything the operator typed expecting to hear it back — the welcome
    message, the closing line — is written in here rather than hoped for. The
    guardrails go into the global node for the same reason, and because a rule
    that only applies on the node that happened to mention it is not a rule.

    Returns the definition. Mutating in place would be fine and reads worse at
    the call site.
    """
    nodes = definition.get("nodes")
    if not isinstance(nodes, list):
        return definition

    global_node = next((n for n in nodes if n.get("type") == "globalNode"), None)
    if global_node is None:
        global_node = {
            "id": "global-brief",
            "type": "globalNode",
            "position": {"x": -320.0, "y": 0.0},
            "data": {"name": "Rules"},
        }
        nodes.insert(0, global_node)
    global_node.setdefault("data", {})["prompt"] = _global_prompt(brief)

    for node in nodes:
        data = node.setdefault("data", {})
        if node.get("type") == "startCall":
            # Spoken disclosure is a DPDP obligation on a recorded call and
            # every call here is recorded. Defaulted on rather than left to be
            # remembered, matching what the Start Call node does by default.
            data.setdefault("recording_disclosure_enabled", True)
            if brief.welcome_message.strip():
                data["greeting"] = brief.welcome_message.strip()
                data["greeting_type"] = "text"
        elif node.get("type") == "endCall" and brief.closing_line.strip():
            data["prompt"] = (
                f"{data.get('prompt', '').strip()}\n\n"
                f"Close with exactly this line: {brief.closing_line.strip()}"
            ).strip()

    return definition


def workflow_name(brief: AgentBrief) -> str:
    """What the agent is called in the list, from whatever the brief has."""
    for candidate in (brief.use_case, brief.agent_name, brief.company_name):
        if candidate and candidate.strip():
            return candidate.strip()[:80]
    return "Voice Agent"
