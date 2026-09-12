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
    FLOW_STANDARD,
    FLOW_VOICE,
    STEP_AFTER_CALL,
    STEP_CUSTOMISE,
    STEP_DESCRIBE,
    STEP_HIRE,
    STEP_INTERVIEW,
    STEP_NUMBER,
    STEP_ONBOARDING,
    STEP_PREVIEW,
    STEP_REQUIREMENTS,
    STEP_TEST,
    STEP_VOICE_AND_BRAIN,
    blank_flow,
    flow,
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


class TestTheVoiceFlow:
    def test_hearing_it_comes_before_any_form(self):
        steps = [step["key"] for step in hire_steps(_pack())]
        assert steps[0] == STEP_INTERVIEW
        assert steps[-1] == STEP_HIRE

    def test_the_listening_happens_twice_and_both_are_needed(self):
        """The interview is the published role, before setup, and it decides
        whether somebody continues at all. The preview is their own agent with
        their voice and their business name, and it catches a wrong voice
        before a real caller hears it."""
        steps = [step["key"] for step in hire_steps(_pack())]
        assert steps.index(STEP_INTERVIEW) < steps.index(STEP_PREVIEW)
        assert steps.index(STEP_PREVIEW) < steps.index(STEP_TEST)

    def test_the_preset_is_picked_before_anything_is_heard_or_tested(self):
        steps = [step["key"] for step in hire_steps(_pack())]
        assert steps.index(STEP_VOICE_AND_BRAIN) < steps.index(STEP_PREVIEW)

    def test_the_preset_chips_are_named_not_inlined(self):
        """So a preset added to model_presets appears in the flow without an
        edit here, and the price a chip shows is the one that gets charged."""
        step = next(s for s in hire_steps(_pack()) if s["key"] == STEP_VOICE_AND_BRAIN)
        assert step["presets_from"] == "model_presets"

    def test_a_pack_that_asks_for_nothing_does_not_get_an_empty_form(self):
        steps = [step["key"] for step in hire_steps(_pack())]
        assert STEP_ONBOARDING not in steps
        assert STEP_REQUIREMENTS not in steps
        assert STEP_AFTER_CALL not in steps

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
        step = next(s for s in hire_steps(pack) if s["key"] == STEP_ONBOARDING)
        assert step["facts"][0]["question"] == "When are you open?"
        assert step["blocking"] is True

    def test_an_optional_fact_does_not_block_going_live(self):
        pack = _pack(
            required_facts=[
                RequiredFact(key="tagline", question="Any tagline?", required=False)
            ]
        )
        step = next(s for s in hire_steps(pack) if s["key"] == STEP_ONBOARDING)
        assert step["blocking"] is False


class TestWhatHappensAfterTheCall:
    def _with_after_call(self, **kwargs):
        return _pack(
            after_call_apps=[
                RequiredConnector(
                    app="whatsapp",
                    label="WhatsApp",
                    used_for="Sending the confirmation.",
                    required=False,
                )
            ],
            **kwargs,
        )

    def test_it_is_set_up_before_the_test_not_after(self):
        """The thing most worth testing is whether the confirmation actually
        arrived, and it cannot arrive if this has not been set up."""
        steps = [step["key"] for step in hire_steps(self._with_after_call())]
        assert steps.index(STEP_AFTER_CALL) < steps.index(STEP_TEST)

    def test_it_carries_the_apps_so_credentials_can_be_connected_there(self):
        step = next(
            s
            for s in hire_steps(self._with_after_call())
            if s["key"] == STEP_AFTER_CALL
        )
        assert step["connectors"][0]["app"] == "whatsapp"

    def test_an_app_cannot_be_both_a_requirement_and_an_after_call_app(self):
        """Two connect buttons for one credential, and connecting the first
        leaves the second sitting red."""
        connector = RequiredConnector(app="whatsapp", label="WhatsApp", used_for="x")
        with pytest.raises(ValidationError) as raised:
            _pack(required_connectors=[connector], after_call_apps=[connector])
        assert "actually used" in str(raised.value)

    def test_the_same_after_call_app_twice_is_refused(self):
        connector = RequiredConnector(app="whatsapp", label="WhatsApp", used_for="x")
        with pytest.raises(ValidationError):
            _pack(after_call_apps=[connector, connector])


class TestTheStandardFlow:
    """A back-office role: nothing to hear, everything to fit.

    Built against a synthetic non-voice template rather than a real one,
    because the point of the test is that the format now permits this at all.
    """

    def _silent_template(self, monkeypatch):
        from api.services.agent_templates._base import (
            AgentTemplate,
            CallDirection,
            RecommendedStack,
            ScheduleShape,
        )

        template = AgentTemplate(
            id="stuck_orders_sweep",
            name="Stuck Orders Sweep",
            vertical="E-commerce operations",
            direction=CallDirection.scheduled,
            summary="Finds orders stuck at one status too long and lists them.",
            languages=["en"],
            stack=RecommendedStack(llm_provider="anthropic"),
            schedule_shape=ScheduleShape(runs="every morning"),
            nodes=[],
            edges=[],
            guardrails=[],
            compliance_notes=[],
            example_requests=["orders stuck in transit"],
        )
        monkeypatch.setattr(
            "api.services.packs._base.get_template",
            lambda template_id: template if template_id == template.id else None,
        )
        return template

    def test_a_silent_role_can_now_exist_at_all(self, monkeypatch):
        """It could not before: speech and telephony were mandatory on every
        template, so the monthly plan had nothing to sell."""
        self._silent_template(monkeypatch)
        pack = _pack(
            channels=[Channel.SCHEDULED],
            template_id="stuck_orders_sweep",
            demo_number=None,
        )
        assert flow(pack) == FLOW_STANDARD
        assert pricing(pack)["is_hire"] is False

    def test_it_is_never_asked_to_be_heard_or_given_a_number(self, monkeypatch):
        self._silent_template(monkeypatch)
        pack = _pack(
            channels=[Channel.SCHEDULED],
            template_id="stuck_orders_sweep",
            demo_number=None,
        )
        steps = [step["key"] for step in hire_steps(pack)]
        assert STEP_INTERVIEW not in steps
        assert STEP_PREVIEW not in steps
        assert STEP_NUMBER not in steps
        assert STEP_VOICE_AND_BRAIN not in steps

    def test_the_dry_run_blocks_going_live(self, monkeypatch):
        """What decides whether a back-office agent is any good is whether it
        read the right rows and wrote the right thing. Only the dry run shows
        that, so it is not skippable."""
        self._silent_template(monkeypatch)
        pack = _pack(
            channels=[Channel.SCHEDULED],
            template_id="stuck_orders_sweep",
            demo_number=None,
        )
        steps = hire_steps(pack)
        assert [step["key"] for step in steps][-1] == STEP_HIRE
        test_step = next(s for s in steps if s["key"] == STEP_TEST)
        assert test_step["blocking"] is True
        assert STEP_CUSTOMISE in {s["key"] for s in steps}

    def test_after_call_apps_are_refused_on_something_that_never_calls(
        self, monkeypatch
    ):
        self._silent_template(monkeypatch)
        with pytest.raises(ValidationError):
            _pack(
                channels=[Channel.SCHEDULED],
                template_id="stuck_orders_sweep",
                demo_number=None,
                after_call_apps=[
                    RequiredConnector(app="whatsapp", label="WhatsApp", used_for="x")
                ],
            )


class TestNoneOfTheseFit:
    def test_it_asks_what_the_job_is_then_shows_what_already_does_it(self):
        """Most of the time something already does. A role somebody has hired a
        hundred times beats a first draft."""
        steps = [step["key"] for step in blank_flow()]
        assert steps[0] == STEP_DESCRIBE
        assert steps[1] == "similar"
        assert steps[-1] == STEP_HIRE

    def test_nothing_is_built_without_a_test_first(self):
        test_step = next(s for s in blank_flow() if s["key"] == STEP_TEST)
        assert test_step["blocking"] is True


class TestEveryPackDeclaresAFlow:
    def test_calling_roles_use_the_voice_flow(self):
        for pack in all_packs():
            assert flow(pack) == (FLOW_VOICE if is_calling(pack) else FLOW_STANDARD)

    def test_the_card_says_which_flow_hiring_will_use(self):
        assert card(get_pack("front_desk_clinic"))["flow"] == FLOW_VOICE


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
