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

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CallDirection(str, Enum):
    """What starts this agent.

    Named for calls because calls were all there was. It decides more than it
    looks: inbound agents need a number per branch and little concurrency,
    outbound campaigns need burst concurrency and almost no numbers, and an
    agent nobody rings needs neither.

    The two message values are why the name is now wrong and the field is not:
    every caller reads ``direction`` and renaming it would be a rename across
    six templates, the assembler and the builder for no behavioural gain. The
    values are what matter.
    """

    inbound = "inbound"
    outbound = "outbound"
    #: Somebody messages it -- WhatsApp, email, web chat, Slack.
    message = "message"
    #: Nothing triggers it but the clock. Nobody is waiting on the other end,
    #: which is why this family needs no voice, no number and no escalation.
    scheduled = "scheduled"


#: Directions that put the agent on a phone call. The set that decides money:
#: a calling agent is priced as a hire, everything else runs on the monthly
#: plan. One definition, because the badge, the price, the validator and the
#: hire flow must all agree on what "calling" means.
CALLING_DIRECTIONS: frozenset[CallDirection] = frozenset(
    {CallDirection.inbound, CallDirection.outbound}
)


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

    **Only the LLM is required, and only because it is what makes this an
    agent rather than a scheduled script.** Speech and telephony were
    mandatory here for two good reasons that both turn out to be reasons about
    *voice*: a template with no named providers cannot be priced before the
    first call, and leaving Indian speech to a default produces an agent that
    mishears every third caller. Neither applies to an agent that never
    speaks, and requiring them anyway made it structurally impossible to
    publish one — so the requirement moved to where it earns its place, onto
    the templates that actually make calls.
    """

    llm_provider: str
    llm_model: str = ""
    #: Required for a template that speaks; empty otherwise.
    stt_provider: str = ""
    stt_model: str = ""
    tts_provider: str = ""
    tts_model: str = ""
    #: Required for a template that uses the phone; empty otherwise.
    telephony_provider: str = ""
    #: Why this stack and not another, in one sentence the chat can quote.
    rationale: str = ""

    @property
    def speaks(self) -> bool:
        return bool(self.stt_provider and self.tts_provider)


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


class ScheduleShape(BaseModel):
    """What a run of a scheduled agent looks like.

    The back-office family's answer to ``CallShape``. Its cost is not minutes,
    it is a handful of LLM tokens and some connector calls, so the honest thing
    a card can say is how often it runs and how much it gets through -- not a
    rupee figure that rounds to zero.
    """

    #: A cron-shaped description in words: "every morning", "hourly",
    #: "every evening at 7". Words rather than a cron string because this is
    #: read by a business owner on a card.
    runs: str
    #: Typical items handled per run: orders swept, invoices reconciled.
    typical_items_per_run: int = 0
    typical_runs_per_month: int = 0


class SuggestedVoice(BaseModel):
    """A voice worth hearing on this template: who it sounds like, in what.

    A template card that says "6 languages" is a claim; a play button is
    proof. Each suggestion is one vendor voice, a gender, and the language
    its sample is spoken in, so a gallery can offer a man and a woman in
    Tamil and Hindi side by side rather than one default.
    """

    provider: str
    voice_id: str
    name: str
    gender: str
    #: A language code the sample is recorded in: en, hi, ta, kn, te.
    language: str
    #: The model the sample is recorded on. Part of the identity of a sample,
    #: not a detail of it: the same speaker on two ElevenLabs models does not
    #: sound the same, and the gallery has to ask for the one it recorded.
    model: str = "eleven_multilingual_v2"
    blurb: str = ""


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
    #: Present for a template that makes or takes calls; the figure a customer
    #: decides on is per month, and that needs a call length and a volume.
    call_shape: CallShape | None = None
    #: Present for a scheduled template instead.
    schedule_shape: ScheduleShape | None = None
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
    suggested_voices: list[SuggestedVoice] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _stack_matches_the_trigger(self) -> "AgentTemplate":
        """A calling template must name speech and telephony; others need not.

        The rule that used to be enforced by making the fields mandatory,
        now scoped to the templates it is actually about. Stated as a check
        rather than as a type so the error says which template and what is
        missing, and so the same file can hold a WhatsApp agent.
        """
        calling = self.direction in CALLING_DIRECTIONS
        if calling:
            missing = [
                name
                for name, value in (
                    ("stt_provider", self.stack.stt_provider),
                    ("tts_provider", self.stack.tts_provider),
                    ("telephony_provider", self.stack.telephony_provider),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    f"template {self.id!r} makes or takes calls and must name "
                    f"{', '.join(missing)}: without them it cannot be priced "
                    "before the first call, and Indian speech left to a "
                    "default mishears every third caller"
                )
            if self.call_shape is None:
                raise ValueError(
                    f"template {self.id!r} makes or takes calls and needs a "
                    "call_shape; the figure a customer decides on is monthly"
                )
        elif self.direction == CallDirection.scheduled and (
            self.schedule_shape is None
        ):
            raise ValueError(
                f"template {self.id!r} is scheduled and needs a schedule_shape"
            )
        return self

    @property
    def speaks(self) -> bool:
        """Whether this template is on a phone call."""
        return self.direction in CALLING_DIRECTIONS

    # Nullable views onto ``call_shape``, so the several screens and tools that
    # quote a monthly figure do not each have to remember that a WhatsApp agent
    # has no minutes. Returning None rather than zero on purpose: "this agent
    # uses no minutes" and "this agent uses zero minutes a month" read the same
    # in a number and differently in a sentence.
    @property
    def call_seconds(self) -> int | None:
        return self.call_shape.typical_call_seconds if self.call_shape else None

    @property
    def calls_per_month(self) -> int | None:
        return self.call_shape.typical_calls_per_month if self.call_shape else None

    @property
    def minutes_per_month(self) -> int | None:
        return self.call_shape.minutes_per_month if self.call_shape else None

    @property
    def start_node(self) -> TemplateNode | None:
        for node in self.nodes:
            if node.type == "startCall":
                return node
        return None
