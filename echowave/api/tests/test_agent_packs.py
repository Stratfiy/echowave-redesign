"""The pack format, and the things a publisher must not be able to get wrong.

Most of these are billing tests wearing a product costume. A badge, a price and
a setup flow all come off one declaration, so a pack that lies about its
channels lies about all three at once -- and two of the three ways it can go
wrong cost real money quietly.
"""

import pytest
from pydantic import ValidationError

from api.services.packs import (
    INCLUDED_EXECUTIONS,
    INCLUDED_MINUTES_PER_SEAT,
    PLATFORM_PRICE_PAISE,
    SEAT_PRICE_PAISE,
    AgentPack,
    Channel,
    Publisher,
    RequiredConnector,
    RequiredFact,
    all_packs,
    badges,
    card,
    catalogue,
    get_pack,
    hire_steps,
    is_calling,
    jobs,
    listed_packs,
    pricing,
)
from api.services.packs.derive import (
    CHANNEL_LABELS,
    STEP_CONNECT,
    STEP_FACTS,
    STEP_GO_LIVE,
    STEP_HEAR_IT,
    STEP_NUMBER,
)

AGENCY = Publisher(slug="org_7", name="Some Agency")


def _pack(**overrides) -> AgentPack:
    base = dict(
        slug="test_pack",
        name="Front Desk",
        job="Answer the phone",
        summary="Answers the phone.",
        publisher=AGENCY,
        channels=[Channel.INBOUND_CALL],
        template_id="clinic_appointment",
        demo_number="+911234567890",
    )
    base.update(overrides)
    return AgentPack(**base)


class TestAPublisherCannotMisdeclare:
    def test_a_pack_that_works_nowhere_is_refused(self):
        with pytest.raises(ValidationError):
            _pack(channels=[])

    def test_a_pack_naming_a_template_that_does_not_exist_fails_on_definition(self):
        """Not at hire time. A broken listing must never reach a customer."""
        with pytest.raises(ValidationError):
            _pack(template_id="no_such_template")

    def test_a_voice_template_behind_a_text_only_pack_is_refused(self):
        """The contradiction that costs money silently: badged as text, priced
        as text, and it still dials real people."""
        with pytest.raises(ValidationError) as raised:
            _pack(channels=[Channel.WHATSAPP])
        assert "calling channel" in str(raised.value)

    def test_claiming_outbound_on_an_inbound_only_template_is_refused(self):
        with pytest.raises(ValidationError) as raised:
            _pack(channels=[Channel.OUTBOUND_CALL], template_id="clinic_appointment")
        assert "inbound only" in str(raised.value)

    def test_a_calling_pack_cannot_be_listed_without_a_demo_number(self):
        """A voice agent sold on a screenshot is sold on a promise."""
        with pytest.raises(ValidationError) as raised:
            _pack(demo_number=None, listed=True)
        assert "demo number" in str(raised.value)

    def test_an_unlisted_calling_pack_may_still_lack_one(self):
        """Its own publisher can hire it while it is being written."""
        assert _pack(demo_number=None, listed=False).slug == "test_pack"

    def test_the_same_fact_twice_is_refused(self):
        fact = RequiredFact(key="business_name", question="Name?")
        with pytest.raises(ValidationError):
            _pack(required_facts=[fact, fact])

    def test_the_same_app_twice_is_refused(self):
        connector = RequiredConnector(app="whatsapp", label="WhatsApp", used_for="x")
        with pytest.raises(ValidationError):
            _pack(required_connectors=[connector, connector])

    def test_a_negative_creator_price_is_refused(self):
        with pytest.raises(ValidationError):
            _pack(creator_price_paise=-1)


class TestTheBadgeIsDerived:
    def test_calling_reads_as_a_promise_not_as_a_channel_name(self):
        assert badges(_pack(channels=[Channel.INBOUND_CALL, Channel.WHATSAPP])) == [
            "Answers calls",
            "WhatsApp",
        ]

    def test_outbound_says_makes_calls(self):
        pack = _pack(
            channels=[Channel.OUTBOUND_CALL], template_id="ecom_cod_confirmation"
        )
        assert badges(pack) == ["Makes calls"]

    def test_calling_comes_first_whatever_order_it_was_declared_in(self):
        pack = _pack(
            channels=[Channel.WHATSAPP, Channel.SCHEDULED, Channel.INBOUND_CALL]
        )
        assert badges(pack)[0] == "Answers calls"

    def test_every_channel_has_a_label(self):
        """A channel added without a label would still appear on the card, but
        under its raw slug. This test is what stops that shipping."""
        for channel in Channel:
            assert channel in CHANNEL_LABELS


class TestTheBadgeIsThePriceTag:
    def test_a_calling_pack_is_a_hire(self):
        priced = pricing(_pack())
        assert priced["is_hire"] is True
        assert priced["seat_price_paise"] == SEAT_PRICE_PAISE
        assert priced["platform_price_paise"] == 0
        assert priced["included_minutes"] == INCLUDED_MINUTES_PER_SEAT
        assert priced["overage_unit"] == "minute"

    def test_a_creator_price_is_added_on_top_of_ours_not_instead_of_it(self):
        priced = pricing(_pack(creator_price_paise=200_000))
        assert priced["monthly_price_paise"] == SEAT_PRICE_PAISE + 200_000

    def test_the_platform_price_is_per_business_not_per_agent(self):
        """Non-calling agents cost nothing to run and the product is worth more
        the more of them somebody has, so they are never a seat."""
        assert PLATFORM_PRICE_PAISE < SEAT_PRICE_PAISE
        assert INCLUDED_EXECUTIONS > 0

    def test_is_calling_and_pricing_agree(self):
        for pack in all_packs():
            assert pricing(pack)["is_hire"] == is_calling(pack)


class TestTheStepsAreGenerated:
    def test_hearing_it_comes_before_any_form(self):
        steps = [step["key"] for step in hire_steps(_pack())]
        assert steps[0] == STEP_HEAR_IT
        assert steps[-1] == STEP_GO_LIVE

    def test_a_pack_that_asks_for_nothing_does_not_get_an_empty_form(self):
        steps = [step["key"] for step in hire_steps(_pack())]
        assert STEP_FACTS not in steps
        assert STEP_CONNECT not in steps

    def test_only_an_inbound_pack_is_asked_to_choose_a_number(self):
        inbound = [step["key"] for step in hire_steps(_pack())]
        outbound = [
            step["key"]
            for step in hire_steps(
                _pack(
                    channels=[Channel.OUTBOUND_CALL],
                    template_id="ecom_cod_confirmation",
                )
            )
        ]
        assert STEP_NUMBER in inbound
        assert STEP_NUMBER not in outbound

    def test_the_facts_step_carries_the_questions_the_pack_declared(self):
        pack = _pack(
            required_facts=[
                RequiredFact(key="opening_hours", question="When are you open?")
            ]
        )
        step = next(s for s in hire_steps(pack) if s["key"] == STEP_FACTS)
        assert step["facts"][0]["question"] == "When are you open?"
        assert step["blocking"] is True

    def test_an_optional_fact_does_not_block_going_live(self):
        pack = _pack(
            required_facts=[
                RequiredFact(key="tagline", question="Any tagline?", required=False)
            ]
        )
        step = next(s for s in hire_steps(pack) if s["key"] == STEP_FACTS)
        assert step["blocking"] is False


class TestTheShelf:
    def test_every_pack_we_publish_is_valid_and_first_party(self):
        packs = all_packs()
        assert packs
        for pack in packs:
            assert pack.publisher.first_party is True
            assert pack.creator_price_paise == 0
            assert pack.summary and pack.job and pack.name

    def test_no_two_packs_share_a_slug(self):
        slugs = [pack.slug for pack in all_packs()]
        assert len(slugs) == len(set(slugs))

    def test_the_shelf_is_empty_until_a_demo_number_is_configured(self):
        """An empty shelf is a missing configuration somebody notices; a shelf
        full of dead demo links is one nobody reports."""
        without = catalogue._packs(None)
        assert all(not pack.listed for pack in without)
        with_number = catalogue._packs("+911234567890")
        assert all(pack.listed for pack in with_number)
        assert all(pack.demo_number == "+911234567890" for pack in with_number)

    def test_listed_packs_never_includes_an_unlisted_one(self):
        assert all(pack.listed for pack in listed_packs())

    def test_get_pack_is_by_slug_and_says_nothing_for_an_unknown_one(self):
        assert get_pack("front_desk_clinic").name == "Front Desk"
        assert get_pack("nope") is None

    def test_every_pack_asks_who_to_transfer_to(self):
        """An agent with no way to hand a call to a person is an agent that
        guesses when something is real."""
        for pack in all_packs():
            assert "escalation_number" in {fact.key for fact in pack.required_facts}, (
                pack.slug
            )

    def test_facts_shared_between_packs_use_the_same_key(self):
        """The reason hiring a second agent is quicker than the first: the
        answers live in organisation_facts under one key per question."""
        front_desk = get_pack("front_desk_clinic")
        reservations = get_pack("reservations_desk")
        shared = {f.key for f in front_desk.required_facts} & {
            f.key for f in reservations.required_facts
        }
        assert {"business_name", "opening_hours", "location"} <= shared

    def test_a_card_carries_what_the_shelf_needs_and_the_price_it_computed(self):
        rendered = card(get_pack("front_desk_clinic"))
        assert rendered["badges"][0] == "Answers calls"
        assert rendered["pricing"]["is_hire"] is True
        assert rendered["publisher"]["name"] == "Decibyl"

    def test_the_jobs_on_the_shelf_follow_the_order_packs_declare_them(self):
        """Not alphabetical. The shelf is ordered by what we want somebody to
        hire first, and answering the phone is the wedge."""
        shelf = catalogue._packs("+911234567890")
        assert jobs(shelf)[0] == "Answer the phone"
        assert len(jobs(shelf)) == len({pack.job for pack in shelf})
