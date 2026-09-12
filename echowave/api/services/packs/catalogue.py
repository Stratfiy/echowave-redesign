"""The shelf: every pack Decibyl publishes.

Organised by job, not by channel. "Answers the phone when the desk is busy" is
a job; "inbound voice agent with tool calling" is a feature, and nobody hires a
feature.

Each pack wraps a template that already carries the prompts, the Indic-first
stack and the compliance notes, and adds the three things hiring needs: what
the agent has to know about the business, what it needs connected, and where it
works. The card, the price and the setup steps are computed from those.

Every calling pack is unlisted until a demo line exists. That is the rule doing
its job rather than a gap: a voice agent on a card with no number to ring is a
promise, and the demo call is the thing that sells it.

How a prospect reaches it comes from the database -- one agent marked
``is_demo`` -- rather than from configuration, so changing which agent
demonstrates the product needs no redeploy. Two ways are derived from that one
flag: its share link, which needs no telephony at all, and a number pointed at
it, which is stronger proof for a product whose pitch is that it answers your
phone. Either is enough to list a role; requiring the number would gate the
whole shelf on a telephony purchase. ``PACK_DEMO_NUMBER`` remains as a fallback
for a deployment with no demo agent, and for synchronous callers that cannot
await a query.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Iterable, Optional

from loguru import logger

from api.constants import PACK_DEMO_NUMBER
from api.db import db_client
from api.services.packs._base import (
    AgentPack,
    Channel,
    FactKind,
    Publisher,
    RequiredConnector,
    RequiredFact,
)

DECIBYL = Publisher(slug="decibyl", name="Decibyl", first_party=True)

#: Facts nearly every agent needs. Written once because they land in
#: ``organisation_facts`` under the same keys -- so a business that answered
#: them while hiring its front desk is not asked again by its order
#: confirmation agent. Sharing the keys is the entire point.
_BUSINESS_NAME = RequiredFact(
    key="business_name",
    question="What is your business called?",
    example="Narayani Dental",
    used_for="How the agent introduces itself on every call.",
)
_OPENING_HOURS = RequiredFact(
    key="opening_hours",
    question="When are you open?",
    kind=FactKind.HOURS,
    example="Mon-Sat 9:30am-8pm, closed Sunday",
    used_for='Answering "are you open now", and never offering a slot you are closed for.',
)
_LOCATION = RequiredFact(
    key="location",
    question="Where are you located?",
    kind=FactKind.LONG_TEXT,
    example="2nd floor, above HDFC Bank, Hosur Main Road",
    used_for="Giving directions, which is one of the three things callers ask most.",
)
_ESCALATION_NUMBER = RequiredFact(
    key="escalation_number",
    question="Who should it transfer to when something is real?",
    kind=FactKind.PHONE,
    example="+91 98765 43210",
    used_for="Handing the call to a person instead of guessing.",
)

#: Filed under after-call, not requirements. It is not something the agent
#: needs to do the job -- it is what the customer gets once the job is done.
_WHATSAPP_CONFIRMATION = RequiredConnector(
    app="whatsapp",
    label="WhatsApp",
    used_for="Sending the confirmation after the call, so the customer keeps a record.",
    required=False,
)
_GOOGLE_CALENDAR = RequiredConnector(
    app="googlecalendar",
    label="Google Calendar",
    used_for="Writing the appointment into the calendar your staff already watch.",
)


def _packs(
    demo_number: Optional[str], demo_url: Optional[str] = None
) -> tuple[AgentPack, ...]:
    """Build the catalogue against whatever ways a prospect can hear the demo.

    Takes them as arguments rather than reading configuration directly so the
    rule is testable: the same catalogue with and without a way to be heard
    must produce a listed and an unlisted shelf.
    """
    listed = bool(demo_number or demo_url)

    return (
        AgentPack(
            slug="front_desk_clinic",
            name="Front Desk",
            job="Answer the phone",
            summary="Answers every call, books the appointment, and hands anything clinical to a person.",
            publisher=DECIBYL,
            channels=[Channel.INBOUND_CALL, Channel.WHATSAPP],
            template_id="clinic_appointment",
            industries=["Clinics", "Dental", "Diagnostics", "Salons"],
            languages=["en", "hi", "ta", "te", "kn"],
            required_facts=[
                _BUSINESS_NAME,
                _OPENING_HOURS,
                _LOCATION,
                RequiredFact(
                    key="practitioners",
                    question="Who do patients book with?",
                    kind=FactKind.LIST,
                    example="Dr Anitha (Mon-Wed), Dr Ravi (Thu-Sat)",
                    used_for="Offering the right person, and the right days.",
                ),
                RequiredFact(
                    key="consultation_fee",
                    question="What does a consultation cost?",
                    example="₹300 for a first visit",
                    used_for="The single most-asked question on a clinic line.",
                ),
                _ESCALATION_NUMBER,
            ],
            required_connectors=[_GOOGLE_CALENDAR],
            after_call_apps=[_WHATSAPP_CONFIRMATION],
            demo_number=demo_number,
            demo_url=demo_url,
            listed=listed,
        ),
        AgentPack(
            slug="order_confirmation",
            name="Order Confirmation",
            job="Confirm orders before they ship",
            summary="Rings every COD order before dispatch and confirms the customer still wants it.",
            publisher=DECIBYL,
            channels=[Channel.OUTBOUND_CALL, Channel.WHATSAPP],
            template_id="ecom_cod_confirmation",
            industries=["E-commerce", "D2C", "Logistics"],
            languages=["en", "hi", "ta", "te", "kn"],
            required_facts=[
                _BUSINESS_NAME,
                RequiredFact(
                    key="dispatch_cutoff",
                    question="When do you cut off dispatch each day?",
                    example="4pm",
                    used_for="Deciding how late it is worth calling to save a shipment.",
                ),
                RequiredFact(
                    key="return_policy",
                    question="What is your return policy, in one sentence?",
                    kind=FactKind.LONG_TEXT,
                    example="7-day return, unused, original packaging",
                    used_for="Answering the question that makes people cancel on the call.",
                ),
                _ESCALATION_NUMBER,
            ],
            required_connectors=[
                RequiredConnector(
                    app="shopify",
                    label="Shopify",
                    used_for="Reading the order and writing the confirmation back against it.",
                ),
            ],
            after_call_apps=[_WHATSAPP_CONFIRMATION],
            demo_number=demo_number,
            demo_url=demo_url,
            listed=listed,
        ),
        AgentPack(
            slug="payment_reminder",
            name="Payment Reminder",
            job="Chase what is owed",
            summary="Calls before the due date, takes the promise to pay, and never calls outside legal hours.",
            publisher=DECIBYL,
            channels=[Channel.OUTBOUND_CALL, Channel.WHATSAPP],
            template_id="lending_payment_reminder",
            industries=["Lending", "NBFC", "Subscriptions", "Rentals"],
            languages=["en", "hi", "ta", "te", "kn"],
            required_facts=[
                _BUSINESS_NAME,
                RequiredFact(
                    key="payment_link_base",
                    question="Where should customers pay?",
                    example="https://pay.yourbrand.in",
                    used_for="Sending a link they can act on instead of a number to remember.",
                ),
                _ESCALATION_NUMBER,
            ],
            after_call_apps=[_WHATSAPP_CONFIRMATION],
            demo_number=demo_number,
            demo_url=demo_url,
            listed=listed,
        ),
        AgentPack(
            slug="lead_qualifier",
            name="Lead Qualifier",
            job="Qualify new enquiries",
            summary="Calls a new lead within minutes, finds out what they actually want, and books the visit.",
            publisher=DECIBYL,
            channels=[Channel.OUTBOUND_CALL, Channel.WHATSAPP],
            template_id="real_estate_lead_qual",
            industries=["Real estate", "Interiors", "Solar", "B2B services"],
            languages=["en", "hi", "ta", "te", "kn"],
            required_facts=[
                _BUSINESS_NAME,
                RequiredFact(
                    key="what_you_sell",
                    question="What are you selling them?",
                    kind=FactKind.LONG_TEXT,
                    example="2 and 3BHK flats in Whitefield, ₹85L-1.4Cr",
                    used_for="Qualifying against something real instead of a script.",
                ),
                _ESCALATION_NUMBER,
            ],
            after_call_apps=[_WHATSAPP_CONFIRMATION],
            demo_number=demo_number,
            demo_url=demo_url,
            listed=listed,
        ),
        AgentPack(
            slug="admissions_desk",
            name="Admissions Desk",
            job="Follow up on admissions",
            summary="Calls every enquiry back, answers fees and batches, and books the counselling slot.",
            publisher=DECIBYL,
            channels=[Channel.OUTBOUND_CALL, Channel.WHATSAPP],
            template_id="edtech_admissions",
            industries=["Edtech", "Coaching", "Colleges", "Skilling"],
            languages=["en", "hi", "ta", "te", "kn"],
            required_facts=[
                _BUSINESS_NAME,
                RequiredFact(
                    key="courses",
                    question="What courses are you admitting for?",
                    kind=FactKind.LIST,
                    example="NEET repeater batch, JEE foundation",
                    used_for="Answering what they rang about without transferring.",
                ),
                RequiredFact(
                    key="fees",
                    question="What do they cost?",
                    kind=FactKind.LONG_TEXT,
                    example="₹1.2L for the year, instalments allowed",
                    used_for="The question that decides whether they come in.",
                ),
                _ESCALATION_NUMBER,
            ],
            after_call_apps=[_WHATSAPP_CONFIRMATION],
            demo_number=demo_number,
            demo_url=demo_url,
            listed=listed,
        ),
        AgentPack(
            slug="reservations_desk",
            name="Reservations Desk",
            job="Answer the phone",
            summary="Takes the booking, holds the table, and stops the phone ringing through service.",
            publisher=DECIBYL,
            channels=[Channel.INBOUND_CALL, Channel.WHATSAPP],
            template_id="restaurant_reservation",
            industries=["Restaurants", "Cafes", "Cloud kitchens"],
            languages=["en", "hi", "ta", "te", "kn"],
            required_facts=[
                _BUSINESS_NAME,
                _OPENING_HOURS,
                _LOCATION,
                RequiredFact(
                    key="covers",
                    question="How many can you seat?",
                    kind=FactKind.NUMBER,
                    example="40",
                    used_for="Knowing when to stop taking bookings.",
                ),
                _ESCALATION_NUMBER,
            ],
            after_call_apps=[_WHATSAPP_CONFIRMATION],
            demo_number=demo_number,
            demo_url=demo_url,
            listed=listed,
        ),
    )


@lru_cache(maxsize=1)
def _cached() -> tuple[AgentPack, ...]:
    """The shelf as configuration alone describes it.

    Cached because it is pure. Callers that can await a query should prefer
    :func:`resolve_packs`, which asks the database which number is the demo
    line; this is the answer for synchronous callers and for a deployment that
    has not marked one.
    """
    return _packs(PACK_DEMO_NUMBER)


async def resolve_packs() -> tuple[AgentPack, ...]:
    """Every pack, with the demo line read from the telephony admin.

    Not cached. The query is one indexed boolean lookup, and caching it would
    mean somebody marking a demo line in the admin and then wondering why the
    shelf is still empty -- which is exactly the confusion the env var caused.
    """
    contact: dict[str, Optional[str]] = {}
    try:
        contact = await db_client.demo_contact()
    except Exception as error:  # noqa: BLE001
        # A shelf that cannot read the demo agent falls back to configuration
        # rather than failing. Worst case the calling roles stay unlisted,
        # which is the same state as having marked no demo agent.
        logger.warning("Could not read the demo agent: {}", error)
    return _packs(
        contact.get("number") or PACK_DEMO_NUMBER,
        contact.get("url"),
    )


async def resolve_listed_packs() -> tuple[AgentPack, ...]:
    """The shelf a customer browses, with the live demo line."""
    return tuple(pack for pack in await resolve_packs() if pack.listed)


async def resolve_pack(slug: str) -> Optional[AgentPack]:
    for pack in await resolve_packs():
        if pack.slug == slug:
            return pack
    return None


def all_packs() -> tuple[AgentPack, ...]:
    """Every pack, listed or not. For our own screens and for tests."""
    return _cached()


def listed_packs() -> tuple[AgentPack, ...]:
    """The shelf a customer browses, as configuration describes it."""
    return tuple(pack for pack in _cached() if pack.listed)


def get_pack(slug: str) -> Optional[AgentPack]:
    for pack in _cached():
        if pack.slug == slug:
            return pack
    return None


def jobs(packs: Optional[Iterable[AgentPack]] = None) -> tuple[str, ...]:
    """The job groups on the shelf, in the order packs first declare them.

    Insertion order rather than alphabetical: the shelf is ordered by what we
    want somebody to hire first, and "Answer the phone" is the wedge.

    Takes the packs so the ordering rule can be tested without a configured
    demo number, which the cached shelf depends on.
    """
    seen: dict[str, None] = {}
    for pack in listed_packs() if packs is None else packs:
        seen.setdefault(pack.job, None)
    return tuple(seen)
