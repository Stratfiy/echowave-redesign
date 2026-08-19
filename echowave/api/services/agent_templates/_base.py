"""Schema for a starting-point agent, ready to run.

A template is not a code generator. It carries the two things an authoring LLM
cannot invent reliably — **a production prompt** and **a stack that is right for
Indian traffic** — and leaves the SDK TypeScript to `create_workflow`, which
already validates and returns machine-readable errors the model can correct
against. Templates written as source strings would rot the first time the SDK
changed; written as structure, they survive it.

Three properties keep a template honest.

**The prompts are the deliverable.** A node's `prompt` here is meant to go into
a live workflow unedited. That is the difference between a template and an
example: an example shows the shape, a template answers the call. Anything
placeholder-shaped belongs in `template_variables`, not in the prompt text.

**The stack is priced, not asserted.** `stt`, `llm`, `tts` and `telephony` name
providers the rate card already prices, so `estimate_agent_cost` can quote the
template before a single call is placed. A template naming an unpriced provider
would produce a quote with a hole in it.

**Compliance is part of the template, not a footnote.** An Indian outbound
collections agent that calls at 21:00 is a regulatory problem, not a tuning
problem. Where a vertical carries a legal constraint, it is written into
`guardrails` so it reaches the prompt, and into `compliance_notes` so it reaches
the person deploying it.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class CallDirection(str, Enum):
    """Which way the call goes.

    It decides more than it looks: inbound agents need a number per branch and
    little concurrency, outbound campaigns need burst concurrency and almost no
    numbers. The plan a customer should buy follows from this field.
    """

    inbound = "inbound"
    outbound = "outbound"


class TemplateNode(BaseModel):
    """One node of the starting workflow.

    Field names match `api/services/workflow/dto.py` so the authoring LLM can
    map a template onto SDK calls without a translation table.
    """

    #: `startCall` | `agent` | `endCall` | `globalNode` — see `list_node_types`.
    type: str
    #: Short identifier, shown on the canvas and in call logs.
    name: str
    #: The instruction the agent runs on. Written to be used as-is.
    prompt: str
    #: First words spoken. `startCall` only; omitted elsewhere.
    greeting: str | None = None
    #: Variables this node should capture from the conversation, as
    #: `{"name": "extraction hint"}`. Empty when the node captures nothing.
    extract: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class TemplateEdge(BaseModel):
    """A transition, with the condition that fires it."""

    source: str
    target: str
    label: str
    condition: str

    model_config = ConfigDict(extra="forbid")


class RecommendedStack(BaseModel):
    """Providers to configure the agent with.

    Indic-first by default. Sarvam's Saarika and Bulbul handle Indian languages
    and code-switching that Western models mangle, and they are the cheapest
    priced rows in the card — the rare case where the better choice for the
    traffic is also the cheaper one.
    """

    stt_provider: str
    stt_model: str = ""
    llm_provider: str
    llm_model: str = ""
    tts_provider: str
    tts_model: str = ""
    telephony_provider: str
    #: Why this stack and not another, in one sentence the chat can quote.
    rationale: str = ""


class CallShape(BaseModel):
    """What a typical call on this template looks like.

    Used to turn a per-minute rate into a monthly number, which is the figure a
    customer actually decides on. These are starting assumptions to be replaced
    by the account's own traffic once it has some.
    """

    typical_call_seconds: int
    typical_calls_per_month: int

    @property
    def minutes_per_month(self) -> int:
        return round(self.typical_call_seconds * self.typical_calls_per_month / 60)


class AgentTemplate(BaseModel):
    """A vertical's starting agent: stack, flow, prompts and constraints."""

    id: str
    name: str
    #: The business this is for, in the words that business would use.
    vertical: str
    direction: CallDirection
    #: One line for a picker.
    summary: str
    #: Languages the prompts are written to handle.
    languages: list[str]
    stack: RecommendedStack
    call_shape: CallShape
    nodes: list[TemplateNode]
    edges: list[TemplateEdge]
    #: Rules that must survive into the deployed agent. These are behavioural
    #: constraints, not style — several are legal.
    guardrails: list[str]
    #: What the operator has to know before this dials a real person.
    compliance_notes: list[str]
    #: Things a user might type that should land on this template. Lets the
    #: chat match intent without the model guessing.
    example_requests: list[str]
    #: Values the operator must supply before going live. Referenced in prompts
    #: as `{{name}}`, so an unfilled one is visible rather than silently spoken.
    template_variables: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @property
    def start_node(self) -> TemplateNode | None:
        for node in self.nodes:
            if node.type == "startCall":
                return node
        return None
