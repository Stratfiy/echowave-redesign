"""The pack format, and the things a publisher must not be able to get wrong.

Most of these are billing tests wearing a product costume. A badge, a price and
a setup flow all come off one declaration, so a pack that lies about its
channels lies about all three at once -- and two of the three ways it can go
wrong cost real money quietly.
"""

import pytest
from pydantic import ValidationError

from api.services.packs import (
    UNIT_MINUTE,
    UNIT_RUN,
    AgentPack,
    Channel,
    Publisher,
    RequiredConnector,
    RequiredFact,
    all_packs,
    badges,
    card,
    catalogue,
    charging,
    get_pack,
    hire_steps,
    is_calling,
    jobs,
    listed_packs,
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


class TestHowARoleDrawsOnCredit:
    """There is no per-pack price, and that is the point.

    A pack used to carry a seat price of Rs6,999 a month for a voice agent and
    Rs4,999 for everything else, and NOTHING IN services/billing EVER READ
    EITHER. They reached only the card and the builder chat, which quoted
    "Rs6,999 a month" while billing charged the subscription plan instead. We
    quoted one number and charged another.
    """

    def test_a_calling_role_is_metered_by_the_minute_and_needs_voice(self):
        charge = charging(_pack())
        assert charge["needs_voice"] is True
        assert charge["unit"] == UNIT_MINUTE

    def test_a_role_that_answers_nothing_is_metered_by_the_run(self):
        quiet = _pack(
            channels=[Channel.WHATSAPP, Channel.WEB],
            template_id="internal_knowledge",
            demo_url=None,
            demo_number=None,
        )
        charge = charging(quiet)
        assert charge["needs_voice"] is False
        assert charge["unit"] == UNIT_RUN

    def test_hiring_costs_nothing_of_itself(self):
        """Unlimited bots is what the fixed platform charge buys, and a
        per-agent fee would contradict it."""
        for pack in all_packs():
            assert charging(pack)["hire_price_paise"] == 0, pack.slug

    def test_a_creator_price_survives_as_the_one_per_pack_amount(self):
        """A third-party listing is a real thing the format supports -- see
        Publisher.first_party -- so the publisher's own charge stays."""
        assert (
            charging(_pack(creator_price_paise=200_000))["creator_price_paise"]
            == 200_000
        )
        assert all(charging(p)["creator_price_paise"] == 0 for p in all_packs())

    def test_no_rupee_figure_for_running_it_is_baked_into_a_pack(self):
        """What a minute costs depends on the voice and brain the ACCOUNT
        chose -- about Rs3 on the managed Indic stack, twice that on a premium
        English voice. A figure baked into a card goes stale the moment
        somebody changes their voice, which is how the last one came to quote
        a price billing never charged."""
        for pack in all_packs():
            keys = set(charging(pack))
            assert not {k for k in keys if "per_minute" in k or "monthly" in k}

    def test_needs_voice_and_is_calling_agree(self):
        for pack in all_packs():
            assert charging(pack)["needs_voice"] == is_calling(pack)


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
        assert charging(pack)["needs_voice"] is False

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

    def test_no_calling_role_is_listed_until_a_demo_is_configured(self):
        """A shelf full of dead demo links is one nobody reports.

        Narrowed to the roles it is about. It used to assert the whole shelf
        goes dark without a demo number, which was true only while every role
        answered a phone -- a role that never calls has nothing to demonstrate
        and is correctly listed regardless.

        That is a better product, not a loosened test: with no demo number
        configured at all there are still two roles somebody can hire.
        """
        without = catalogue._packs(None)
        calling = [p for p in without if is_calling(p)]
        quiet = [p for p in without if not is_calling(p)]
        assert calling and quiet, "this test needs both kinds on the shelf"
        assert all(not pack.listed for pack in calling)
        assert all(pack.listed for pack in quiet)

        with_number = catalogue._packs("+911234567890")
        assert all(pack.listed for pack in with_number)
        assert all(
            pack.demo_number == "+911234567890"
            for pack in with_number
            if is_calling(pack)
        )

    def test_a_role_that_never_calls_is_given_no_demo_number(self):
        """A demo number on a bot that cannot answer one is a link to nowhere,
        printed on the card as proof."""
        for pack in catalogue._packs("+911234567890"):
            if not is_calling(pack):
                assert pack.demo_number is None, pack.slug
                assert pack.demo_url is None, pack.slug

    def test_listed_packs_never_includes_an_unlisted_one(self):
        assert all(pack.listed for pack in listed_packs())

    def test_get_pack_is_by_slug_and_says_nothing_for_an_unknown_one(self):
        assert get_pack("front_desk_clinic").name == "Front Desk Bot"
        assert get_pack("nope") is None

    def test_every_calling_pack_asks_who_to_transfer_to(self):
        """An agent with no way to hand a call to a person is an agent that
        guesses when something is real.

        Calling packs only: there is no call to transfer out of a bot that
        answers a question in a chat or runs at eight in the morning. Asking
        for an escalation number anyway would be a required field with nothing
        behind it, which teaches people to type anything into our forms.
        """
        for pack in all_packs():
            if not is_calling(pack):
                continue
            assert "escalation_number" in {fact.key for fact in pack.required_facts}, (
                pack.slug
            )

    def test_a_non_calling_pack_declares_no_after_call_apps(self):
        """Enforced by the format too; asserted here so the catalogue is
        checked as well as the schema."""
        for pack in all_packs():
            if is_calling(pack):
                continue
            assert pack.after_call_apps == [], pack.slug

    def test_facts_shared_between_packs_use_the_same_key(self):
        """The reason hiring a second agent is quicker than the first: the
        answers live in organisation_facts under one key per question."""
        front_desk = get_pack("front_desk_clinic")
        reservations = get_pack("reservations_desk")
        shared = {f.key for f in front_desk.required_facts} & {
            f.key for f in reservations.required_facts
        }
        assert {"business_name", "opening_hours", "location"} <= shared

    def test_a_card_carries_what_the_shelf_needs_and_how_it_charges(self):
        rendered = card(get_pack("front_desk_clinic"))
        assert rendered["badges"][0] == "Answers calls"
        assert rendered["charging"]["needs_voice"] is True
        assert rendered["publisher"]["name"] == "Decibyl"

    def test_the_jobs_on_the_shelf_follow_the_order_packs_declare_them(self):
        """Not alphabetical. The shelf is ordered by what we want somebody to
        hire first, and answering the phone is the wedge."""
        shelf = catalogue._packs("+911234567890")
        assert jobs(shelf)[0] == "Answer the phone"
        assert len(jobs(shelf)) == len({pack.job for pack in shelf})
