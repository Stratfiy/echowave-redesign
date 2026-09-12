"""What an agent can actually be asked to do with a connected app, authored.

A connector gives an agent *access*. It does not tell it *when to act*, and
that sentence is the whole difference between a working agent and one that
emails a confirmation at the wrong moment. Until now the chat handed a model a
raw action slug and asked it to write that sentence freehand, per customer,
every time -- so the instruction governing a live phone call was improvised by
a model rather than authored by anyone who knows the app.

A skill is the missing rung: **pack (the role) → skill (one job inside it) →
tool (one API action).** It carries the exact action slug, so nothing is
invented, and a ``use_when`` written for an agent mid-call rather than for a
desk assistant.

Two rules, both learned the hard way in this repository:

**Additive, never a gate.** A skill is a shortcut past guesswork, not an
allowlist. When nothing here covers what a business asked for, the raw action
list is still offered -- hiding a capability because we had not written a card
for it is the silent-absence failure, and the catalogue vendor adds actions
every week.

**Written for a caller on the line.** "Use when the caller has confirmed a date
and time and given their name" is a rule an agent can follow mid-sentence.
"Manage calendar events" is not. Every ``use_when`` here names the moment, and
several name the moment *not* to act, because the expensive failures are all
premature: the payment link sent before the amount was agreed, the
confirmation emailed to an address nobody read back.
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

#: Composio action slugs are upper snake case, prefixed by their toolkit. The
#: pattern is checked rather than trusted because a typo here ships an agent
#: that fails on its first real call, and the failure is a caller hearing
#: "I couldn't do that" with no way for us to know why.
_ACTION = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$")


class AppSkill(BaseModel):
    """One job an agent can be given on one connected app."""

    #: Stable identifier, used by the chat to attach it. Named for the job, not
    #: the API: a person choosing "email-the-confirmation" knows what they are
    #: agreeing to; "GMAIL_SEND_EMAIL" they do not.
    slug: str
    #: Composio toolkit slug this belongs to, e.g. ``gmail``.
    app: str
    #: The exact Composio action. The reason skills exist: a model asked to
    #: send email writes ``GMAIL_SEND``, which does not exist, and the failure
    #: lands mid-call.
    action: str
    #: What the agent will call it, in the operator's language. This reaches
    #: the model as the tool's name, so it is the first thing it reads when
    #: deciding whether to act.
    name: str
    #: When to use it -- and, where it matters, when not to. Written for an
    #: agent mid-conversation.
    use_when: str
    #: One line for a person choosing from a list.
    does: str

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("action")
    @classmethod
    def _looks_like_an_action(cls, value: str) -> str:
        if not _ACTION.match(value):
            raise ValueError(
                f"{value!r} is not shaped like a Composio action slug "
                "(GMAIL_SEND_EMAIL). A typo here ships an agent that fails on "
                "its first real call."
            )
        return value

    @model_validator(mode="after")
    def _action_belongs_to_its_app(self) -> "AppSkill":
        # A calendar action filed under gmail is a tool that cannot work,
        # created without complaint. The prefix is the only check available
        # without a network call, and it catches the copy-paste.
        prefix = self.app.replace("_", "").upper()
        if not self.action.replace("_", "").startswith(prefix):
            raise ValueError(
                f"skill {self.slug!r} is filed under {self.app!r} but its "
                f"action {self.action!r} does not belong to it"
            )
        return self


#: The skills we have written, by the app they act on.
#:
#: Deliberately short. Every entry is a claim that we know when an agent should
#: reach for it on a live call, and a hundred half-considered entries would be
#: a claim about nothing -- the same reasoning the connector catalogue's
#: POPULAR list carries.
SKILLS: tuple[AppSkill, ...] = (
    AppSkill(
        slug="email-the-confirmation",
        app="gmail",
        action="GMAIL_SEND_EMAIL",
        name="Email the confirmation",
        does="Sends the caller a written confirmation of what was agreed.",
        use_when=(
            "Use after the booking or order is confirmed AND the caller has "
            "given an email address that you read back to them. Do not use it "
            "on an address you inferred, and do not use it to chase somebody "
            "who has not agreed to anything yet."
        ),
    ),
    AppSkill(
        slug="email-the-quote",
        app="gmail",
        action="GMAIL_SEND_EMAIL",
        name="Email the quote",
        does="Sends the price you quoted, in writing, after the call.",
        use_when=(
            "Use when you have given the caller a price and they asked for it "
            "in writing. Send the figure you actually said on the call -- a "
            "quote that differs from what they heard is worse than none."
        ),
    ),
    AppSkill(
        slug="log-the-call",
        app="googlesheets",
        action="GOOGLESHEETS_BATCH_UPDATE",
        name="Log the call in the sheet",
        does="Appends what happened to the sheet the office already watches.",
        use_when=(
            "Use once at the end of a call, after the outcome is settled. "
            "Never mid-call: a row written before the caller finishes is a row "
            "that says the wrong thing."
        ),
    ),
    AppSkill(
        slug="send-the-payment-link",
        app="razorpay",
        action="RAZORPAY_CREATE_PAYMENT_LINK",
        name="Send the payment link",
        does="Creates a payment link for an amount the caller has agreed to.",
        use_when=(
            "Use only after the caller has agreed to the exact amount and you "
            "have read it back. Never to 'see what they say' -- a link sent "
            "before agreement reads as a demand, and this one asks a customer "
            "for money."
        ),
    ),
    AppSkill(
        slug="check-payment-status",
        app="razorpay",
        action="RAZORPAY_FETCH_PAYMENT",
        name="Check whether they have paid",
        does="Looks up whether a payment has actually landed.",
        use_when=(
            "Use when the caller says they have already paid, before agreeing "
            "or disagreeing. Saying 'we have not received it' without checking "
            "is how an account loses a customer who did pay."
        ),
    ),
    AppSkill(
        slug="check-order-status",
        app="shopify",
        action="SHOPIFY_GET_ORDER",
        name="Check the order",
        does="Looks up an order so you can tell the caller where it is.",
        use_when=(
            "Use as soon as the caller gives an order number or the phone "
            "number they ordered with. Do not describe an order's status from "
            "memory or from what the caller tells you it should be."
        ),
    ),
    AppSkill(
        slug="post-to-the-channel",
        app="slack",
        action="SLACK_SEND_MESSAGE",
        name="Tell the team",
        does="Posts to the channel the team already watches.",
        use_when=(
            "Use when something needs a human and cannot wait for somebody to "
            "open a dashboard: an angry caller, a booking you could not take, "
            "a question you could not answer. One message per call at most."
        ),
    ),
)


def for_app(app: str) -> tuple[AppSkill, ...]:
    """The skills written for one connected app, or empty if we have none."""
    slug = (app or "").strip().lower()
    return tuple(skill for skill in SKILLS if skill.app == slug)


def get(slug: str) -> Optional[AppSkill]:
    """One skill by its slug, or None. Never raises on an invented name."""
    wanted = (slug or "").strip().lower()
    return next((skill for skill in SKILLS if skill.slug == wanted), None)


def apps() -> tuple[str, ...]:
    """Apps we have written skills for, in the order they were written."""
    seen: list[str] = []
    for skill in SKILLS:
        if skill.app not in seen:
            seen.append(skill.app)
    return tuple(seen)
