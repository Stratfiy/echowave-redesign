"""What the graph is allowed to believe exists, per vertical.

Graphiti extracts entities with an LLM, and left to itself it will invent a
type system from whatever it read: a "Person", a "Caller", a "Patient" and a
"Mr Kumar" as four separate kinds of thing. Passing ``entity_types`` replaces
that guesswork with a fixed vocabulary, and the vocabulary is the vertical --
a clinic knows about patients and appointments, a quote desk knows about lanes
and carriers, and neither should be extracting the other's nouns.

So these are packs, not a global schema. Adding a vertical means adding a pack
here; it must never mean widening the clinic's types until they cover
everybody, which is how a schema becomes a shrug.

**Every field is optional, and that is the point.** A required field tells the
model it must produce a value, and a model that must produce a phone number
will produce one. The same rule already governs transition arguments: an
invented appointment time read back to a patient is how a wrong booking
reaches a real diary, and a missing one is merely a question the agent has to
ask. Missing beats wrong, every time, on every field here.

Descriptions are written for the extracting model, not for a developer. They
say what the field is *in a phone call*, because that is what it will be
reading -- a transcript in Tamil, Hindi and English at once, not a form.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

#: Every pack's name, as it appears in configuration.
CLINIC_PACK = "clinic"


class Patient(BaseModel):
    """A person the clinic treats, or who is asking to be treated."""

    phone_number: str | None = Field(
        default=None,
        description=(
            "The number this person can be reached on, only if it was said "
            "out loud or is known from the call. Never the clinic's own "
            "number."
        ),
    )
    spoken_name: str | None = Field(
        default=None,
        description=(
            "The name the person gave for themselves, spelled as it sounded. "
            "Do not correct it to a more common spelling."
        ),
    )
    is_new_patient: bool | None = Field(
        default=None,
        description=(
            "True only if they said they have not visited before. Leave empty "
            "if it did not come up."
        ),
    )


class Appointment(BaseModel):
    """A specific slot in the diary, wanted or held."""

    spoken_time: str | None = Field(
        default=None,
        description=(
            "The date and time exactly as the caller or the agent said it -- "
            "'next Thursday eleven o'clock', 'naalaikku morning'. Do not "
            "convert it to a date. A wrong conversion is worse than none."
        ),
    )
    treatment: str | None = Field(
        default=None,
        description="What the appointment is for, in the caller's own words.",
    )
    status: str | None = Field(
        default=None,
        description=(
            "One of booked, rescheduled, cancelled, or requested. Use "
            "requested unless the agent confirmed it was done."
        ),
    )


class Treatment(BaseModel):
    """A procedure the clinic offers or the patient asked about."""

    spoken_name: str | None = Field(
        default=None,
        description="What it was called in the conversation, not its clinical name.",
    )


class Practitioner(BaseModel):
    """A doctor or hygienist, named in the call."""

    spoken_name: str | None = Field(
        default=None, description="The name as it was said, title included."
    )


class Concern(BaseModel):
    """What is actually wrong, in the patient's words."""

    description: str | None = Field(
        default=None,
        description=(
            "The complaint as the patient described it. Keep their words: "
            "'pain in the back tooth since Friday', not 'dental pain'."
        ),
    )
    urgency: str | None = Field(
        default=None,
        description=(
            "Only if they said so themselves -- 'unbearable', 'can wait'. "
            "Never your own judgement of how urgent it sounds."
        ),
    )


#: The clinic pack. Keys are the type names the extracting model is given, so
#: they read as nouns a transcript would contain.
CLINIC_ENTITY_TYPES: dict[str, type[BaseModel]] = {
    "Patient": Patient,
    "Appointment": Appointment,
    "Treatment": Treatment,
    "Practitioner": Practitioner,
    "Concern": Concern,
}

#: Packs by name. One vertical today; adding the quote desk means adding a
#: second entry, not editing the first.
ENTITY_TYPE_PACKS: dict[str, dict[str, type[BaseModel]]] = {
    CLINIC_PACK: CLINIC_ENTITY_TYPES,
}


def entity_types_for_pack(pack: str | None) -> dict[str, type[BaseModel]] | None:
    """The types to extract with, or None to let Graphiti decide.

    An unknown pack returns None rather than raising: a typo in configuration
    should degrade extraction to Graphiti's own defaults, not stop an
    organization's calls from being recorded at all.
    """
    if not pack:
        return None
    return ENTITY_TYPE_PACKS.get(pack)
