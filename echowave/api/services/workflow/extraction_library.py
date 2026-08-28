"""Ready-made extractions, so nobody starts from an empty prompt box.

``ExtractionSpec`` has been configurable since the QA node grew
``qa_extractions``, and the node editor renders it generically. What it does
not do is help: the operator gets an empty Name and an empty Instructions box,
and the quality of what comes back is entirely the quality of the prompt they
happened to write. "Customer name" sounds like it needs no instructions until
the model returns the *agent's* name, or a name it assembled out of a
half-heard syllable, or the name of the person the caller was talking about.

So this is a catalog of the extractions people actually ask for, each with a
prompt that has the failure modes written into it. Picking one from the library
is a starting point, not a contract — an entry is copied into the node's own
``qa_extractions`` on add, and editing it afterwards edits that copy. Nothing
here is referenced by id at runtime, which is deliberate: an operator who tunes
a prompt for their own calls must not have it silently replaced the next time
this file is edited.

**These are prompts, so they live with the code that renders them into
instructions**, not in the frontend. The QA pass appends them to one system
prompt (see ``render_extraction_instructions``), the phrasing here is written
to sit inside that fragment, and the SDK and API get the same catalog the UI
does rather than a second copy that drifts.

Adding an entry is adding a ``LibraryExtraction`` to ``_LIBRARY``. Categories
are free-form strings, ordered by first appearance, so a new industry section
needs no registration anywhere.

It sits here rather than under ``qa/`` for an import reason worth knowing:
``QANodeData`` in ``dto`` names this catalog, ``qa/__init__`` imports
``analysis``, and ``analysis`` imports ``dto`` — so reaching it through the
``qa`` package would make ``dto`` import itself half-initialised.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

#: The catalog name a ``PropertySpec`` points at via
#: ``renderer_options.library.catalog``. One catalog today; the indirection
#: exists so the next list-valued field with a library does not need a second
#: endpoint, a second dialog, or a special case in the renderer.
CATALOG_QA_EXTRACTIONS = "qa_extractions"


class LibraryExtraction(BaseModel):
    """One catalog entry, and the ``ExtractionSpec`` it seeds."""

    key: str = Field(description="Stable id for this entry. Not stored on the node.")
    display_name: str = Field(description="What the picker lists it as.")
    category: str = Field(description="Section heading in the picker.")
    summary: str = Field(description="One line on what this pulls out of a call.")

    # The ExtractionSpec payload, field-for-field. Kept as plain fields rather
    # than a nested ExtractionSpec so the catalog can be served without the
    # node-spec machinery, and so an entry that predates a new spec field
    # still validates.
    name: str = Field(description="Seeds ExtractionSpec.name — the JSON key.")
    prompt: str = Field(description="Seeds ExtractionSpec.prompt.")
    answer_type: str = Field(default="free_text")
    predefined_options: str = Field(default="")
    expected_format: str = Field(default="text")


class ExtractionLibraryResponse(BaseModel):
    """The catalog, and the order its sections should be shown in."""

    catalog: str
    categories: list[str]
    extractions: list[LibraryExtraction]


_ALL_INDUSTRIES = "All industries"
_SALES = "Sales and lead qualification"
_SUPPORT = "Customer support"
_HEALTHCARE = "Healthcare"
_EDUCATION = "Education"
_COLLECTIONS = "Collections and finance"


_LIBRARY: tuple[LibraryExtraction, ...] = (
    # ---------------------------------------------------------------- general
    LibraryExtraction(
        key="customer_name",
        display_name="Customer name",
        category=_ALL_INDUSTRIES,
        summary="The name of the person on the call, when they actually gave it.",
        name="customer_name",
        prompt=(
            "The name of the person the agent was speaking to.\n"
            "Return the name only, as plain text — no salutation, no role, no "
            "relationship descriptor.\n"
            "Take it only when the caller states it themselves, confirms it "
            "when the agent reads it back, or acts on the agent using it.\n"
            "Do not take the agent's own name. Do not take the name of "
            "somebody the caller talks about. Do not assemble a name out of "
            "partial or unclear audio.\n"
            "If more than one name is said, return the one identifying the "
            "person on the call. If none was clearly given, return an empty "
            "string."
        ),
        expected_format="text",
    ),
    LibraryExtraction(
        key="callback_number",
        display_name="Callback number",
        category=_ALL_INDUSTRIES,
        summary="A phone number the caller asked to be reached on.",
        name="callback_number",
        prompt=(
            "A phone number the caller gave for being contacted again.\n"
            "Return digits only, keeping any country code as spoken.\n"
            "Take it only when the caller reads it out or confirms it read "
            "back to them. Speech-to-text mishears digits, so if the number "
            "was never confirmed and the audio was unclear, return an empty "
            "string rather than a guess — a wrong number here is worse than "
            "no number.\n"
            "Do not return the number that was dialled unless the caller "
            "explicitly said to use it."
        ),
        expected_format="text",
    ),
    LibraryExtraction(
        key="customer_email",
        display_name="Email address",
        category=_ALL_INDUSTRIES,
        summary="An email address the caller spelled out or confirmed.",
        name="customer_email",
        prompt=(
            "The caller's email address.\n"
            "Take it only when spelled out or confirmed when read back. "
            "Spoken email is badly served by transcription — 'dot', 'at' and "
            "letter-by-letter spelling all arrive inconsistently — so return "
            "an empty string unless the address was confirmed."
        ),
        expected_format="email",
    ),
    LibraryExtraction(
        key="call_outcome",
        display_name="Call outcome",
        category=_ALL_INDUSTRIES,
        summary="What the call actually ended in, as one of your own categories.",
        name="call_outcome",
        prompt=(
            "How this call ended, judged on what was agreed rather than on "
            "tone.\n"
            "Choose 'resolved' only when the caller's reason for the call was "
            "actually dealt with on this call. Use 'follow_up_required' when "
            "something was promised or left open, 'not_interested' when the "
            "caller declined, 'wrong_number' when the person reached was not "
            "who was wanted, and 'no_meaningful_conversation' when the call "
            "ended before anything was discussed."
        ),
        answer_type="predefined",
        predefined_options=(
            "resolved, follow_up_required, not_interested, wrong_number, "
            "no_meaningful_conversation"
        ),
    ),
    LibraryExtraction(
        key="callback_requested",
        display_name="Callback requested",
        category=_ALL_INDUSTRIES,
        summary="Whether the caller asked to be called back.",
        name="callback_requested",
        prompt=(
            "True when the caller asked to be contacted again, or accepted an "
            "offer to be called back.\n"
            "The agent offering a callback is not enough on its own — the "
            "caller has to have accepted it."
        ),
        expected_format="boolean",
    ),
    LibraryExtraction(
        key="callback_time",
        display_name="Preferred callback time",
        category=_ALL_INDUSTRIES,
        summary="When the caller asked to be reached, in their own words.",
        name="callback_time",
        prompt=(
            "When the caller asked to be contacted again.\n"
            "Prefer an exact date and time when one was agreed. When the "
            "caller was vague — 'after lunch', 'sometime next week', 'evening' "
            "— return their own words rather than inventing a precise time "
            "they did not commit to.\n"
            "Return an empty string when no callback was discussed."
        ),
        expected_format="timestamp",
    ),
    LibraryExtraction(
        key="language_spoken",
        display_name="Language spoken",
        category=_ALL_INDUSTRIES,
        summary="The language the caller actually used, for routing later calls.",
        name="language_spoken",
        prompt=(
            "The language the caller mostly spoke in.\n"
            "Name the language in English — 'Hindi', 'Tamil', 'English'. "
            "Where the caller mixed languages, name the one they used for "
            "most of their own turns rather than the one the agent opened in. "
            "Say 'mixed' only when neither language dominates."
        ),
        expected_format="text",
    ),
    LibraryExtraction(
        key="escalation_required",
        display_name="Escalation required",
        category=_ALL_INDUSTRIES,
        summary="Whether this call needs a human to pick it up.",
        name="escalation_required",
        prompt=(
            "True when this call needs a person to follow up.\n"
            "Set it when the caller asked for a human, when they raised a "
            "complaint the agent could not settle, when they threatened to "
            "leave or to escalate, or when the agent promised something it "
            "cannot itself do.\n"
            "A caller merely being frustrated is not enough — the test is "
            "whether anything is actually left for a person to do."
        ),
        expected_format="boolean",
    ),
    LibraryExtraction(
        key="unanswered_questions",
        display_name="Unanswered questions",
        category=_ALL_INDUSTRIES,
        summary="What the caller asked that the agent could not answer.",
        name="unanswered_questions",
        prompt=(
            "Questions the caller asked that the agent did not answer, or "
            "answered with a refusal or an 'I don't know'.\n"
            "List them as short phrases separated by semicolons, in the "
            "caller's own terms. Return an empty string when everything asked "
            "was answered.\n"
            "This is the list worth reading weekly: it is where the agent's "
            "knowledge base is thin."
        ),
        expected_format="text",
    ),
    LibraryExtraction(
        key="sentiment",
        display_name="Caller sentiment",
        category=_ALL_INDUSTRIES,
        summary="How the caller felt by the end, not how they started.",
        name="sentiment",
        prompt=(
            "The caller's attitude by the end of the call.\n"
            "Judge the end state, not the opening — someone who called in "
            "annoyed and left satisfied is 'positive'. Weigh what they said "
            "over how briskly they said it."
        ),
        answer_type="predefined",
        predefined_options="positive, neutral, negative, frustrated",
    ),
    LibraryExtraction(
        key="do_not_contact",
        display_name="Do-not-contact request",
        category=_ALL_INDUSTRIES,
        summary="Whether the caller asked not to be contacted again.",
        name="do_not_contact",
        prompt=(
            "True when the person asked not to be called again, in any "
            "wording — 'remove me', 'stop calling', 'don't call this number'.\n"
            "Being uninterested in the offer is not the same thing; this is "
            "only for a request about future contact.\n"
            "Err towards true when it is ambiguous. Acting on a false "
            "positive costs one call; missing a real request is a compliance "
            "problem."
        ),
        expected_format="boolean",
    ),
    # ------------------------------------------------------------------ sales
    LibraryExtraction(
        key="lead_score",
        display_name="Lead score",
        category=_SALES,
        summary="How qualified the caller is, 1 to 10.",
        name="lead_score",
        prompt=(
            "How qualified this caller is as a lead, from 1 to 10.\n"
            "1 is someone who said they are not interested. 10 is someone "
            "ready to buy now, with the budget and the authority to do it.\n"
            "Score on what they said about need, timing, budget and "
            "authority — not on how pleasant the conversation was. Return the "
            "number only."
        ),
        expected_format="numeric",
    ),
    LibraryExtraction(
        key="buying_intent",
        display_name="Buying intent",
        category=_SALES,
        summary="How close the caller is to actually buying.",
        name="buying_intent",
        prompt=(
            "How close this caller is to a purchase.\n"
            "'ready_to_buy' needs them to have asked to proceed, to price, or "
            "to next steps. 'evaluating' is active comparison or specific "
            "questions. 'researching' is early interest with no timeline. "
            "'not_interested' is an explicit decline."
        ),
        answer_type="predefined",
        predefined_options=("ready_to_buy, evaluating, researching, not_interested"),
    ),
    LibraryExtraction(
        key="budget_mentioned",
        display_name="Budget mentioned",
        category=_SALES,
        summary="Any figure the caller put on what they can spend.",
        name="budget_mentioned",
        prompt=(
            "A budget or price the caller stated they could spend.\n"
            "Return the figure with its currency as spoken. Take a range as "
            "spoken rather than picking one end of it.\n"
            "Take only what the caller said about their own budget — not a "
            "price the agent quoted, and not a figure the caller was asking "
            "about."
        ),
        expected_format="text",
    ),
    LibraryExtraction(
        key="objection_raised",
        display_name="Objection raised",
        category=_SALES,
        summary="The main reason the caller gave for hesitating.",
        name="objection_raised",
        prompt=(
            "The main reason the caller gave for not going ahead.\n"
            "Pick the one they actually pushed on, not every doubt in "
            "passing. 'no_need' is for not seeing the point of it at all; "
            "'timing' is wanting it later; 'competitor' is already being with "
            "someone else. Use 'none' when they raised no objection."
        ),
        answer_type="predefined",
        predefined_options=(
            "price, timing, no_need, competitor, needs_approval, trust, none"
        ),
    ),
    LibraryExtraction(
        key="demo_scheduled",
        display_name="Demo or meeting scheduled",
        category=_SALES,
        summary="Whether a specific next meeting was actually agreed.",
        name="demo_scheduled",
        prompt=(
            "True only when a specific meeting, demo or site visit was agreed "
            "— a day, or a day and time, that the caller accepted.\n"
            "'Send me something and I'll look' is not a scheduled meeting. "
            "The agent offering a slot the caller did not accept is not "
            "either."
        ),
        expected_format="boolean",
    ),
    # ---------------------------------------------------------------- support
    LibraryExtraction(
        key="issue_category",
        display_name="Issue category",
        category=_SUPPORT,
        summary="What the caller's problem was about.",
        name="issue_category",
        prompt=(
            "What the caller's problem was about.\n"
            "Classify the reason they called, which is not always the topic "
            "they spent longest on. When two apply, choose the one that "
            "would have kept them from calling had it not happened."
        ),
        answer_type="predefined",
        predefined_options=(
            "billing, technical_issue, delivery, account_access, "
            "product_question, complaint, cancellation, other"
        ),
    ),
    LibraryExtraction(
        key="issue_resolved",
        display_name="Issue resolved",
        category=_SUPPORT,
        summary="Whether the caller's problem was actually fixed on this call.",
        name="issue_resolved",
        prompt=(
            "True only when the caller's problem was actually dealt with "
            "during this call.\n"
            "A promise to look into it, a ticket raised, or a transfer to "
            "someone else is not resolved. The agent saying it is resolved is "
            "not enough if the caller did not accept it."
        ),
        expected_format="boolean",
    ),
    LibraryExtraction(
        key="order_reference",
        display_name="Order or ticket reference",
        category=_SUPPORT,
        summary="An order, booking or ticket id said on the call.",
        name="order_reference",
        prompt=(
            "An order, booking, ticket or account reference discussed on the "
            "call.\n"
            "Return it exactly as given, keeping any letters and separators. "
            "Take it only when read out clearly or confirmed when read back — "
            "these are the strings transcription gets wrong most often, and a "
            "corrupted reference is worse than none.\n"
            "When several are mentioned, return the one the call was about."
        ),
        expected_format="text",
    ),
    LibraryExtraction(
        key="refund_requested",
        display_name="Refund requested",
        category=_SUPPORT,
        summary="Whether the caller asked for money back.",
        name="refund_requested",
        prompt=(
            "True when the caller asked for a refund, a reversal, or their "
            "money back in any wording.\n"
            "Asking about the refund policy is not a request. The agent "
            "offering one the caller did not take up is not either."
        ),
        expected_format="boolean",
    ),
    # ------------------------------------------------------------- healthcare
    LibraryExtraction(
        key="appointment_time",
        display_name="Appointment time",
        category=_HEALTHCARE,
        summary="The appointment that was agreed on the call.",
        name="appointment_time",
        prompt=(
            "The date and time of the appointment agreed on this call.\n"
            "Take it only when the caller accepted a specific slot. A slot "
            "the agent offered and the caller did not accept is not an "
            "appointment. When they agreed to a day but not a time, return "
            "the day."
        ),
        expected_format="timestamp",
    ),
    LibraryExtraction(
        key="appointment_action",
        display_name="Appointment action",
        category=_HEALTHCARE,
        summary="Whether the call booked, moved or cancelled a visit.",
        name="appointment_action",
        prompt=(
            "What this call did to an appointment.\n"
            "'rescheduled' needs an existing appointment moved to a new time; "
            "a cancellation followed by a fresh booking is still "
            "'rescheduled'. Use 'none' when appointments were discussed but "
            "nothing changed."
        ),
        answer_type="predefined",
        predefined_options="booked, rescheduled, cancelled, enquiry_only, none",
    ),
    LibraryExtraction(
        key="department_requested",
        display_name="Department or specialty",
        category=_HEALTHCARE,
        summary="The department or specialty the caller asked for.",
        name="department_requested",
        prompt=(
            "The department, specialty or doctor the caller asked for.\n"
            "Return it in the caller's own terms rather than mapping it to an "
            "official department name — 'eye', 'skin doctor' and "
            "'ophthalmology' should each come back as said, because the "
            "mapping belongs to whoever reads this, not to the transcript."
        ),
        expected_format="text",
    ),
    # -------------------------------------------------------------- education
    LibraryExtraction(
        key="course_interest",
        display_name="Course of interest",
        category=_EDUCATION,
        summary="The programme or course the caller asked about.",
        name="course_interest",
        prompt=(
            "The course, programme or subject the caller asked about.\n"
            "Return it as they said it. When they asked about several, return "
            "the one they spent most of the call on."
        ),
        expected_format="text",
    ),
    LibraryExtraction(
        key="enrolment_stage",
        display_name="Enrolment stage",
        category=_EDUCATION,
        summary="How far along the caller is towards enrolling.",
        name="enrolment_stage",
        prompt=(
            "How far this caller has got towards enrolling.\n"
            "'applied' needs them to say they have submitted something. "
            "'ready_to_enrol' is an intention to join with fees or start date "
            "discussed. 'exploring' is general enquiry."
        ),
        answer_type="predefined",
        predefined_options=(
            "exploring, comparing_options, ready_to_enrol, applied, not_interested"
        ),
    ),
    LibraryExtraction(
        key="calling_on_behalf_of",
        display_name="Calling on behalf of",
        category=_EDUCATION,
        summary="Whether the caller is the student or a parent or guardian.",
        name="calling_on_behalf_of",
        prompt=(
            "Whose enrolment this call is about.\n"
            "'self' when the caller is the prospective student. 'parent' or "
            "'guardian' when they are calling about someone else. Decide from "
            "how they refer to the student, not from how they sound."
        ),
        answer_type="predefined",
        predefined_options="self, parent, guardian, other, unclear",
    ),
    # ------------------------------------------------------------ collections
    LibraryExtraction(
        key="payment_commitment",
        display_name="Payment commitment",
        category=_COLLECTIONS,
        summary="Whether the caller committed to paying, and by when.",
        name="payment_commitment",
        prompt=(
            "What the caller committed to paying, and by when.\n"
            "Return the amount and the date together as agreed — 'full "
            "amount by 5 March', 'half by Friday'. Return an empty string "
            "when they made no commitment.\n"
            "An intention to pay 'soon' with no date is not a commitment; "
            "say so in their own words rather than inventing a date."
        ),
        expected_format="text",
    ),
    LibraryExtraction(
        key="dispute_raised",
        display_name="Dispute raised",
        category=_COLLECTIONS,
        summary="Whether the caller disputes that they owe the amount.",
        name="dispute_raised",
        prompt=(
            "True when the caller disputed the debt itself — the amount, "
            "whether they owe it, or whether they already paid.\n"
            "Being unable to pay is not a dispute. This flag exists to route "
            "the call away from collection and towards review, so it should "
            "only fire on a genuine challenge to the amount owed."
        ),
        expected_format="boolean",
    ),
    LibraryExtraction(
        key="hardship_indicated",
        display_name="Financial hardship indicated",
        category=_COLLECTIONS,
        summary="Whether the caller described circumstances needing care.",
        name="hardship_indicated",
        prompt=(
            "True when the caller described circumstances that should change "
            "how this account is handled — job loss, illness, bereavement, or "
            "an explicit inability to pay.\n"
            "This routes an account to a person. Set it on what they said "
            "about their situation, never on tone of voice."
        ),
        expected_format="boolean",
    ),
)


def get_library(catalog: str = CATALOG_QA_EXTRACTIONS) -> ExtractionLibraryResponse:
    """The catalog, with its categories in first-appearance order.

    Ordering comes from ``_LIBRARY`` rather than from sorting, so the sections
    read in the order somebody chose — general first, then by industry —
    instead of alphabetically, which would open on "All industries" only by
    luck.
    """
    if catalog != CATALOG_QA_EXTRACTIONS:
        raise KeyError(catalog)

    categories: list[str] = []
    for entry in _LIBRARY:
        if entry.category not in categories:
            categories.append(entry.category)

    return ExtractionLibraryResponse(
        catalog=catalog,
        categories=categories,
        extractions=list(_LIBRARY),
    )
