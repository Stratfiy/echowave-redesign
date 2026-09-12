"""The format a hirable agent is published in.

A pack is what somebody hires. It is **data, not code**: a declaration of what
the agent does, what it needs to know, and what it needs connected. Decibyl
renders the hiring flow, the badges, the filters and the price from that
declaration -- the publisher never writes onboarding, and never writes a badge.

That split is the whole point of the format.

*The publisher declares; the platform derives.* An agency writes which channels
the agent works on and which connectors it needs. What appears on the card,
what the customer is charged and which setup steps they are walked through are
computed from those declarations. If a publisher could type "answers calls" on
the card, one of them would mislabel it -- and then a clinic hires expecting a
phone agent and gets text messages, or we bill a seat for something that never
dials.

*A contradiction is an error, not a guess.* A pack that declares no calling
channel while wrapping an outbound voice template is refused at definition
time. The alternative is picking one silently, which under-bills and mislabels
in the same move, and nobody finds out.

*A pack wraps a template rather than replacing it.* `agent_templates` already
carries the production prompts, the Indic-first stack and the compliance notes.
Duplicating those here would give us two places to fix a prompt.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.services.agent_templates import get_template
from api.services.agent_templates._base import CallDirection


class Channel(str, Enum):
    """Where the agent does its work.

    Declared by the publisher because it is structural -- it decides which
    runtime the agent runs on. Everything a customer sees about it is derived.
    """

    INBOUND_CALL = "inbound_call"
    OUTBOUND_CALL = "outbound_call"
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    SLACK = "slack"
    TELEGRAM = "telegram"
    WEB = "web"
    #: Runs on a routine rather than in response to anybody.
    SCHEDULED = "scheduled"


#: Channels that put the agent on a phone call. The one set in this file that
#: decides money: any of these and the pack is a hire with a seat price; none
#: of them and it is included in the platform plan.
#:
#: A frozenset rather than a check scattered through the code, because the
#: badge, the filter, the price and the demo-number requirement must all agree
#: on what "calling" means. Four copies of that rule would eventually be three.
CALLING_CHANNELS: frozenset[Channel] = frozenset(
    {Channel.INBOUND_CALL, Channel.OUTBOUND_CALL}
)


class FactKind(str, Enum):
    """What sort of answer a required fact takes.

    Drives the input the hiring flow renders. Deliberately small: a publisher
    who needs a bespoke widget is a publisher writing onboarding again.
    """

    TEXT = "text"
    LONG_TEXT = "long_text"
    PHONE = "phone"
    EMAIL = "email"
    HOURS = "hours"
    LIST = "list"
    NUMBER = "number"


class RequiredFact(BaseModel):
    """Something the agent has to know about this business before it runs.

    Answers land in ``organisation_facts``, which is org-scoped and durable, so
    the second agent somebody hires does not ask the same questions again. That
    is the difference between a setup form and an organisation that remembers.
    """

    key: str
    #: Asked as a question, in the words the business would use. This is the
    #: label on the generated form; "clinic_hours" is a key, "When are you
    #: open?" is a question.
    question: str
    kind: FactKind = FactKind.TEXT
    #: False for something the agent can work without. An optional fact still
    #: appears on the form -- it just does not block going live.
    required: bool = True
    example: str = ""
    #: Where it shows up in the agent's behaviour, in one line. Shown under the
    #: field, because a person filling a form answers better when they know
    #: what it changes.
    used_for: str = ""

    model_config = ConfigDict(extra="forbid")


class RequiredConnector(BaseModel):
    """An outside app this agent cannot do its job without."""

    #: Composio toolkit slug, or one of our built-in apps.
    app: str
    label: str
    #: What the agent does with it, in one line: "writes the appointment into
    #: your calendar". The readiness checklist shows this, so an operator can
    #: tell whether a red dot matters to them.
    used_for: str
    #: False for something that improves the agent without gating it.
    required: bool = True

    model_config = ConfigDict(extra="forbid")


class Publisher(BaseModel):
    """Who published this pack and who earns from it."""

    #: "decibyl" for ours. An organisation id for an agency's.
    slug: str
    name: str
    #: True only for packs we wrote. Drives the badge that says so, and the
    #: review rules -- ours skip the queue, an agency's does not.
    first_party: bool = False

    model_config = ConfigDict(extra="forbid")


class AgentPack(BaseModel):
    """A hirable agent, as published.

    Versioned, because a pack somebody hired in March must keep working when
    the publisher changes it in June. The version is part of the identity of
    what an organisation installed.
    """

    slug: str
    #: The job, in the words a business would use. "Front Desk", not
    #: "Inbound Conversational Agent". Nobody hires a feature.
    name: str
    #: One line, on the card.
    summary: str
    #: The job family, for grouping the shelf. Several packs can share one --
    #: a front desk for a clinic and one for a salon are the same job.
    job: str
    version: str = "1.0.0"
    publisher: Publisher

    #: Structural. Everything the customer sees about capability is derived
    #: from this.
    channels: list[Channel]

    #: The template carrying the prompts, stack and compliance notes. Resolved
    #: against ``agent_templates`` at definition time, so a pack naming a
    #: template that does not exist fails on import rather than at hire time.
    template_id: str

    industries: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)

    required_facts: list[RequiredFact] = Field(default_factory=list)
    #: Apps the agent needs *during* the conversation -- the calendar it reads
    #: to find a free slot, the store it reads the order from.
    required_connectors: list[RequiredConnector] = Field(default_factory=list)
    #: Apps it uses *after* the call, to confirm what happened: the WhatsApp
    #: message, the email receipt, the row written into a sheet.
    #:
    #: A separate list rather than a flag on the one above, because the two are
    #: separate in the runtime too -- in-call tools hang off the node, post-call
    #: actions off ``outcome_actions`` -- and because they are separate
    #: decisions for the person hiring. "What does it need to do the job" and
    #: "what should the customer get afterwards" are asked at different
    #: moments and answered differently, and an agent can be perfectly useful
    #: with neither, one, or both.
    after_call_apps: list[RequiredConnector] = Field(default_factory=list)

    #: How a prospect hears this agent before hiring it. **One of these is
    #: mandatory for anything that makes or takes calls** -- a voice agent sold
    #: on a screenshot is sold on a promise, and the demo is the
    #: highest-converting screen in the product.
    #:
    #: Either will do, and requiring the number specifically would gate the
    #: whole shelf on a telephony purchase. The link costs nothing per demo,
    #: works from the card, reaches a prospect abroad, and its text chat still
    #: works on a network where WebRTC will not connect. The number is
    #: stronger proof for a product whose pitch is that it answers your phone,
    #: so where both exist a card can offer both.
    demo_number: Optional[str] = None
    demo_url: Optional[str] = None

    #: What the publisher charges on top of our seat or platform fee, in paise.
    #: Zero for ours. Theirs to set, like a listing on any marketplace.
    creator_price_paise: int = 0

    #: False while a pack is being written or is awaiting review. Listed packs
    #: are the shelf; unlisted ones are still hirable by their own publisher.
    listed: bool = True

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _coherent(self) -> "AgentPack":
        if not self.channels:
            raise ValueError(
                f"pack {self.slug!r} declares no channels; "
                "a pack that works nowhere cannot be hired"
            )

        template = get_template(self.template_id)
        if template is None:
            raise ValueError(
                f"pack {self.slug!r} names template {self.template_id!r}, "
                "which does not exist"
            )

        calls = bool(set(self.channels) & CALLING_CHANNELS)

        # The contradiction that would otherwise cost money quietly, in both
        # directions. A voice template behind a pack declaring no calling
        # channel would be badged as text, priced as text, and would still dial
        # real people. A silent template behind a pack claiming it calls would
        # charge a seat for something that can never ring anybody.
        #
        # Scoped to what the template actually is. Written unconditionally the
        # first time, when every template was a voice template, it made a
        # non-calling role impossible to publish at all.
        if template.speaks and not calls:
            raise ValueError(
                f"pack {self.slug!r} declares no calling channel but wraps "
                f"voice template {self.template_id!r}; declare inbound_call "
                "or outbound_call, or wrap a non-voice template"
            )
        if calls and not template.speaks:
            raise ValueError(
                f"pack {self.slug!r} declares a calling channel but template "
                f"{self.template_id!r} never makes a call; it would be priced "
                "as a hire and could never ring anybody"
            )

        # And the reverse: an inbound-only template behind a pack claiming it
        # makes outbound calls would be sold as something it cannot do.
        if (
            template.direction == CallDirection.inbound
            and Channel.OUTBOUND_CALL in self.channels
            and Channel.INBOUND_CALL not in self.channels
        ):
            raise ValueError(
                f"pack {self.slug!r} claims outbound calling but template "
                f"{self.template_id!r} is inbound only"
            )

        if calls and self.listed and not (self.demo_number or self.demo_url):
            raise ValueError(
                f"pack {self.slug!r} makes or takes calls and needs a way to "
                "be heard before it can be listed: a demo number, a share "
                "link, or both"
            )

        if self.creator_price_paise < 0:
            raise ValueError(f"pack {self.slug!r} has a negative creator price")

        keys = [fact.key for fact in self.required_facts]
        if len(keys) != len(set(keys)):
            raise ValueError(f"pack {self.slug!r} asks for the same fact twice")

        apps = [connector.app for connector in self.required_connectors]
        if len(apps) != len(set(apps)):
            raise ValueError(f"pack {self.slug!r} requires the same app twice")

        after = [connector.app for connector in self.after_call_apps]
        if len(after) != len(set(after)):
            raise ValueError(f"pack {self.slug!r} names the same after-call app twice")

        # The same app in both lists would render two connect buttons for one
        # credential, and an operator who connected the first would still see
        # the second sitting red.
        both = set(apps) & set(after)
        if both:
            raise ValueError(
                f"pack {self.slug!r} lists {', '.join(sorted(both))} as both a "
                "requirement and an after-call app; pick the moment it is "
                "actually used"
            )

        if self.after_call_apps and not calls:
            raise ValueError(
                f"pack {self.slug!r} declares after-call apps but takes no "
                "calls; there is no call for them to follow"
            )

        return self

    @property
    def template(self):
        """The wrapped template. Present by construction -- the validator
        refuses a pack whose template does not resolve."""
        return get_template(self.template_id)
