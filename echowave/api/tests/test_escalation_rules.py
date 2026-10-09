"""Escalation v2: the rules, decided in code (services/escalation).

No database, no carrier, no model. Done when: each hard trigger transfers at
once; soft signals get one repair and then transfer; an ambiguous sentence
does not escalate but insisting does; frustration only lowers the bar; no
person available turns a transfer into a callback; the ladder rings people
in order, never bridges a machine, never dials an attempt twice, speaks to a
waiting caller and keeps to its cap; and the fallbacks come in order, with a
callback number read back first.
"""

from __future__ import annotations

import asyncio

import pytest

from api.services.escalation import ReasonCode, card, fallbacks, ladder
from api.services.escalation.evaluator import Action, EscalationEvaluator
from api.services.escalation.policy import (
    EscalationPolicy,
    TransferTarget,
    parse,
    validate_changes,
)
from api.services.escalation.signals import human_request, read


def _evaluator(available: bool = True, **policy) -> EscalationEvaluator:
    return EscalationEvaluator(
        EscalationPolicy(**policy), humans_available=lambda: available
    )


# --- hard triggers -------------------------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "Let me talk to a human please",
        "Can I speak to someone?",
        "connect me to your manager",
        "I don't want to talk to a bot",
        "mujhe kisi insaan se baat karni hai",
        "मुझे मैनेजर से बात करनी है",
    ],
)
def test_an_explicit_request_transfers_at_once(said):
    decision = _evaluator().observe_text(said)
    assert decision.action == Action.TRANSFER
    assert decision.reason == ReasonCode.EXPLICIT_REQUEST


@pytest.mark.parametrize(
    "said, topic",
    [
        ("My father has chest pain and can't breathe", "emergency"),
        ("Someone used my card, this is a fraud", "fraud"),
        ("I'll send you a legal notice", "legal_threat"),
    ],
)
def test_a_default_policy_topic_transfers(said, topic):
    decision = _evaluator().observe_text(said)
    assert decision.action == Action.TRANSFER
    assert decision.reason == ReasonCode.POLICY
    assert decision.topic == topic


def test_a_refund_over_the_limit_transfers_and_one_under_does_not():
    over = _evaluator(
        refund_limit=5000,
        always_transfer_topics=["refund_over_limit"],
    ).observe_text("I want a refund of Rs 12,000 for the order")
    assert over.action == Action.TRANSFER and over.topic == "refund_over_limit"

    under = _evaluator(
        refund_limit=5000,
        always_transfer_topics=["refund_over_limit"],
    ).observe_text("Please refund the ₹500 delivery charge")
    assert under.action == Action.NONE


def test_regulated_advice_only_when_the_owner_chose_it():
    said = "Which mutual fund should I invest in?"
    assert _evaluator().observe_text(said).action == Action.NONE
    chosen = _evaluator(always_transfer_topics=["regulated_advice"]).observe_text(said)
    assert chosen.action == Action.TRANSFER and chosen.reason == ReasonCode.POLICY


def test_an_owners_own_phrase_transfers():
    decision = _evaluator(custom_topics=["cancel my membership"]).observe_text(
        "I want to cancel my membership today"
    )
    assert decision.action == Action.TRANSFER and decision.topic == "custom"


def test_the_model_cannot_widen_the_policy():
    # The model reports a topic the owner did not send to a person.
    decision = _evaluator().observe_signal("policy_topic", topic="regulated_advice")
    assert decision.action == Action.NONE
    reported = _evaluator().observe_signal("policy_topic", topic="fraud")
    assert reported.action == Action.TRANSFER and reported.reason == ReasonCode.POLICY


# --- no over-escalation ----------------------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "My manager asked me to call about my order",
        "Can someone help me with my order?",
        "I spoke to a person yesterday about this",
        "I want to know if a person can visit on Monday",
        "I don't want to talk to a human, just book it",
    ],
)
def test_an_ambiguous_sentence_does_not_escalate(said):
    evaluator = _evaluator()
    assert evaluator.observe_text(said).action == Action.NONE
    assert evaluator.decided is None


def test_insisting_is_a_request():
    evaluator = _evaluator()
    assert evaluator.observe_text("Are you a robot?").action == Action.NONE
    assert evaluator.observe_text("What time do you open?").action == Action.NONE
    second = evaluator.observe_text("Is there a real person there?")
    assert second.action == Action.TRANSFER
    assert second.reason == ReasonCode.EXPLICIT_REQUEST


def test_human_request_strengths():
    assert human_request("put me through to a representative") == "strong"
    assert human_request("are you a bot") == "weak"
    assert human_request("what are your hours") is None


# --- soft signals ----------------------------------------------------------------


def test_soft_signals_repair_once_then_transfer():
    evaluator = _evaluator()
    assert evaluator.observe_failure("no_match").action == Action.NONE
    assert evaluator.observe_failure("tool_error").action == Action.NONE
    repair = evaluator.observe_failure("no_match")
    assert repair.action == Action.REPAIR
    # The same score again is not a new signal.
    then = evaluator.observe_failure("no_match")
    assert then.action == Action.TRANSFER
    assert then.reason == ReasonCode.REPAIR_LOOP


def test_the_same_step_failing_twice_is_a_loop():
    evaluator = _evaluator(max_ai_attempts=2)
    assert evaluator.observe_failure("step_failed", step="date").action == Action.NONE
    assert evaluator.observe_failure("step_failed", step="date").action == Action.REPAIR
    final = evaluator.observe_failure("step_failed", step="date")
    assert final.action == Action.TRANSFER and final.reason == ReasonCode.REPAIR_LOOP


def test_no_repair_after_an_explicit_request():
    evaluator = _evaluator()
    evaluator.observe_failure("no_match")
    evaluator.observe_failure("no_match")
    decision = evaluator.observe_text("I want to speak to a human")
    assert decision.action == Action.TRANSFER  # not REPAIR


def test_low_confidence_names_its_reason():
    evaluator = _evaluator()
    for _ in range(3):
        evaluator.observe_signal("low_confidence")
    final = evaluator.observe_signal("low_confidence")
    assert final.action == Action.TRANSFER
    assert final.reason == ReasonCode.LOW_CONFIDENCE


def test_frustration_only_lowers_the_threshold():
    evaluator = _evaluator()
    # On its own, however strong, it never escalates.
    assert evaluator.observe_signal("frustration", level=3).action == Action.NONE
    assert evaluator.observe_failure("no_match").action == Action.NONE
    assert evaluator.observe_failure("no_match").action == Action.REPAIR
    final = evaluator.observe_failure("no_match")
    assert final.action == Action.TRANSFER
    assert final.reason == ReasonCode.FRUSTRATION


def test_silence_is_counted_but_never_transfers_by_itself():
    evaluator = _evaluator()
    for _ in range(6):
        evaluator.observe_quiet()
    assert evaluator.decided is None
    assert evaluator.observe_failure("no_match").action == Action.REPAIR


def test_duplicate_signals_after_a_decision_decide_nothing():
    evaluator = _evaluator()
    assert evaluator.observe_text("talk to a human").action == Action.TRANSFER
    assert evaluator.observe_text("talk to a human!!").action == Action.NONE
    assert evaluator.observe_signal("explicit_request").action == Action.NONE


# --- nobody there ------------------------------------------------------------------


def test_outside_hours_offers_a_callback_instead():
    decision = _evaluator(available=False).observe_text("let me talk to a human")
    assert decision.action == Action.CALLBACK
    assert decision.reason == ReasonCode.EXPLICIT_REQUEST


def test_outside_hours_raises_the_bar_for_soft_signals():
    evaluator = _evaluator(available=False)
    for _ in range(4):
        assert evaluator.observe_failure("no_match").action == Action.NONE
    assert evaluator.observe_failure("no_match").action == Action.REPAIR


def test_transfer_hours_decide_availability():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from api.services.escalation.policy import humans_available

    policy = EscalationPolicy(
        transfer_hours={
            "enabled": True,
            "timezone": "Asia/Kolkata",
            "slots": [{"day_of_week": 0, "start_time": "09:00", "end_time": "18:00"}],
        }
    )
    monday_noon = datetime(2031, 3, 3, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    monday_night = datetime(2031, 3, 3, 23, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert humans_available(policy, now=monday_noon)
    assert not humans_available(policy, now=monday_night)
    # No transfer hours: the agent's own hours, and none at all means open.
    assert humans_available(EscalationPolicy(), None, now=monday_night)


# --- the policy ----------------------------------------------------------------------


def test_a_bad_field_costs_that_field_not_the_policy():
    policy = parse(
        {
            "transfer_numbers": [{"number": "98765 43210"}, {"number": "not a number"}],
            "max_ai_attempts": 99,
            "always_transfer_topics": ["emergency"],
        }
    )
    assert [t.number for t in policy.transfer_numbers] == ["+919876543210"]
    assert policy.max_ai_attempts == 2  # the default, not "off"
    assert policy.always_transfer_topics == ["emergency"]


def test_saving_refuses_what_a_person_can_fix():
    with pytest.raises(Exception):
        validate_changes({"always_transfer_topics": ["weather"]})


def test_defaults_send_emergencies_fraud_and_legal_threats():
    assert EscalationPolicy().always_transfer_topics == [
        "emergency",
        "fraud",
        "legal_threat",
    ]


# --- the ladder ------------------------------------------------------------------------


class FakeDialer:
    """A carrier: each number answers with the outcome it was given."""

    def __init__(self, outcomes: dict[str, str], delay: float = 0.0):
        self.outcomes = outcomes
        self.delay = delay
        self.dialled: list[str] = []
        self.dropped: list[str] = []
        self._futures: dict[str, asyncio.Future] = {}
        self._targets: dict[str, str] = {}

    def watch(self, transfer_id, timeout):
        future = asyncio.get_event_loop().create_future()
        self._futures[transfer_id] = future

        async def wait():
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout)
            except asyncio.TimeoutError:
                return ladder.TIMEOUT

        return asyncio.create_task(wait())

    async def dial(self, target, *, transfer_id, ring_timeout, briefing):
        self.dialled.append(target.number)
        outcome = self.outcomes.get(target.number)
        if outcome == "raise":
            raise RuntimeError("carrier said no")
        if outcome is not None:

            async def answer():
                await asyncio.sleep(self.delay)
                if not self._futures[transfer_id].done():
                    self._futures[transfer_id].set_result(outcome)

            asyncio.create_task(answer())

    async def drop(self, transfer_id):
        self.dropped.append(transfer_id)


class FakeCaller:
    def __init__(self):
        self.events: list[str] = []

    async def start_hold(self):
        self.events.append("hold")

    async def stop_hold(self):
        self.events.append("unhold")

    async def update(self, seconds_waited):
        self.events.append("update")


def _claims(already: set[int] | None = None):
    taken = set(already or set())

    async def claim(expected, transfer_id, label):
        if expected in taken:
            return False
        taken.add(expected)
        return True

    return claim


def _fast_policy(**overrides) -> EscalationPolicy:
    values = dict(
        ring_timeout_seconds=1,
        hold_cap_seconds=5,
        hold_update_seconds=0.3,
    )
    values.update(overrides)
    return EscalationPolicy.model_construct(
        **{**EscalationPolicy().model_dump(), **values}
    )


def _targets(*numbers):
    return [TransferTarget(number=n) for n in numbers]


@pytest.mark.asyncio
async def test_the_ladder_rings_in_order_until_a_person_answers():
    dialer = FakeDialer(
        {
            "+919800000001": ladder.NO_ANSWER,
            "+919800000002": ladder.BUSY,
            "+919800000003": ladder.HUMAN,
        }
    )
    result = await ladder.run(
        targets=_targets("+919800000001", "+919800000002", "+919800000003"),
        policy=_fast_policy(),
        dialer=dialer,
        caller=FakeCaller(),
        claim=_claims(),
        briefing="b",
    )
    assert result.bridged
    assert dialer.dialled == ["+919800000001", "+919800000002", "+919800000003"]
    assert [a.outcome for a in result.attempts] == [
        ladder.NO_ANSWER,
        ladder.BUSY,
        ladder.HUMAN,
    ]
    # Every leg that was not bridged was dropped; the bridged one was not.
    assert len(dialer.dropped) == 2
    assert result.transfer_id not in dialer.dropped


@pytest.mark.asyncio
async def test_a_machine_is_never_bridged():
    dialer = FakeDialer({"+919800000001": ladder.MACHINE})
    result = await ladder.run(
        targets=_targets("+919800000001"),
        policy=_fast_policy(),
        dialer=dialer,
        caller=FakeCaller(),
        claim=_claims(),
        briefing="b",
    )
    assert not result.bridged
    assert result.failure_reason == ladder.MACHINE
    assert dialer.dropped == [result.attempts[0].transfer_id]


@pytest.mark.asyncio
async def test_an_attempt_already_dialled_is_not_dialled_again():
    dialer = FakeDialer({"+919800000002": ladder.HUMAN})
    result = await ladder.run(
        targets=_targets("+919800000001", "+919800000002"),
        policy=_fast_policy(),
        dialer=dialer,
        caller=FakeCaller(),
        claim=_claims(already={0}),
        briefing="b",
    )
    assert dialer.dialled == ["+919800000002"]
    assert result.attempts[0].outcome == ladder.SKIPPED
    assert result.bridged


@pytest.mark.asyncio
async def test_a_waiting_caller_hears_updates_not_only_music():
    caller = FakeCaller()
    dialer = FakeDialer({"+919800000001": ladder.HUMAN}, delay=0.8)
    result = await ladder.run(
        targets=_targets("+919800000001"),
        policy=_fast_policy(ring_timeout_seconds=3),
        dialer=dialer,
        caller=caller,
        claim=_claims(),
        briefing="b",
    )
    assert result.bridged
    assert result.updates_spoken >= 2
    assert caller.events[0] == "hold" and caller.events[-1] == "unhold"
    assert "update" in caller.events


@pytest.mark.asyncio
async def test_the_hold_cap_stops_the_ladder():
    dialer = FakeDialer({})  # nobody answers
    result = await ladder.run(
        targets=_targets(*[f"+91980000000{i}" for i in range(1, 9)]),
        policy=_fast_policy(ring_timeout_seconds=1, hold_cap_seconds=2),
        dialer=dialer,
        caller=FakeCaller(),
        claim=_claims(),
        briefing="b",
    )
    assert not result.bridged
    assert len(dialer.dialled) <= 3
    assert result.waited_seconds < 3.5


@pytest.mark.asyncio
async def test_a_carrier_error_moves_to_the_next_person():
    dialer = FakeDialer({"+919800000001": "raise", "+919800000002": ladder.HUMAN})
    result = await ladder.run(
        targets=_targets("+919800000001", "+919800000002"),
        policy=_fast_policy(),
        dialer=dialer,
        caller=FakeCaller(),
        claim=_claims(),
        briefing="b",
    )
    assert result.bridged
    assert result.attempts[0].outcome == ladder.FAILED


# --- after nobody answered ----------------------------------------------------------------


def test_fallbacks_come_in_order():
    policy = EscalationPolicy(ticket_channel="sms")
    rungs = fallbacks.FallbackLadder(fallbacks.offers_for(policy, can_message=True))
    assert rungs.current() == fallbacks.CALLBACK
    assert rungs.decline() == fallbacks.TICKET
    assert rungs.decline() == fallbacks.VOICEMAIL
    assert rungs.decline() is None


def test_a_ticket_is_skipped_where_nothing_can_send_it():
    policy = EscalationPolicy(ticket_channel="whatsapp")
    assert fallbacks.offers_for(policy, can_message=False) == [
        fallbacks.CALLBACK,
        fallbacks.VOICEMAIL,
    ]


def test_the_explanation_is_honest_and_says_112_for_an_emergency():
    line = fallbacks.explanation(
        reached_nobody=True, outside_hours=False, reason="emergency"
    )
    assert "couldn't reach anyone" in line and "112" in line
    closed = fallbacks.explanation(
        reached_nobody=False, outside_hours=True, reason=None
    )
    assert "available" in closed


# --- the card -------------------------------------------------------------------------------


MESSAGES = [
    {"role": "system", "content": "You are a receptionist."},
    {"role": "user", "content": "Hi, my order 1234 never arrived."},
    {"role": "assistant", "content": "Let me check that."},
    {
        "role": "assistant",
        "tool_calls": [
            {"id": "t1", "function": {"name": "lookup_order", "arguments": "{}"}},
            {"id": "t2", "function": {"name": "issue_credit", "arguments": "{}"}},
        ],
    },
    {"role": "tool", "tool_call_id": "t1", "content": '{"status": "shipped"}'},
    {"role": "tool", "tool_call_id": "t2", "content": '{"status": "failed"}'},
    {"role": "user", "content": "[Note from the platform] repair"},
    {"role": "user", "content": "I want to talk to a person."},
]


@pytest.mark.asyncio
async def test_the_card_carries_what_a_person_needs():
    built = await card.build(
        escalation_uuid="e-1",
        workflow_id=7,
        workflow_run_id=9,
        reason_code="explicit_request",
        reason_detail="asked",
        call_context={"caller_number": "+919876543210", "contact_is_known": True},
        gathered={"customer_name": "Asha", "order_id": "1234", "call_id": "x"},
        messages=MESSAGES,
        language="en-IN",
        consent={"ai_disclosed": True},
        summarise=None,
    )
    assert built["caller"]["name"] == "Asha"
    assert built["caller"]["verification"] == "known number, not verified"
    assert built["fields"] == {"customer_name": "Asha", "order_id": "1234"}
    assert [(a["tool"], a["ok"]) for a in built["actions"]] == [
        ("lookup_order", True),
        ("issue_credit", False),
    ]
    assert built["reason_code"] == "explicit_request"
    assert built["transcript_url"] == "/workflow/7/run/9"
    assert built["language"] == "en-IN" and built["consent"] == {"ai_disclosed": True}
    assert built["summary"]  # the fallback sentence, never empty


@pytest.mark.asyncio
async def test_a_slow_summary_does_not_hold_up_the_card():
    async def slow(_):
        await asyncio.sleep(5)
        return "never"

    built = await card.build(
        escalation_uuid="e-1",
        workflow_id=None,
        workflow_run_id=None,
        reason_code="policy",
        reason_detail="",
        call_context={},
        gathered={},
        messages=MESSAGES,
        language=None,
        consent={},
        summarise=slow,
        timeout=0.05,
    )
    assert "never" not in built["summary"]


@pytest.mark.asyncio
async def test_the_summary_is_two_sentences_and_the_briefing_is_short():
    async def chatty(_):
        return "One. Two. Three. Four."

    built = await card.build(
        escalation_uuid="e-1",
        workflow_id=None,
        workflow_run_id=None,
        reason_code="policy",
        reason_detail="",
        call_context={},
        gathered={"name": "Ravi"},
        messages=MESSAGES,
        language=None,
        consent={},
        summarise=chatty,
    )
    assert built["summary"] == "One. Two."
    spoken = card.spoken_briefing(built)
    assert spoken.startswith("Ravi.") and len(spoken) <= 240


def test_reading_a_sentence_reports_topics_and_amounts():
    reading = read("refund of 2 lakh rupees, this is a scam", refund_limit=100000)
    assert set(reading.topics) == {"fraud", "refund_over_limit"}
    assert reading.refund_amount == 200000


def test_masked_targets_hide_the_number():
    assert ladder.masked(TransferTarget(number="+919876543210", name="Priya")) == (
        "Priya …3210"
    )
