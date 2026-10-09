"""Escalation v2 follow-ups: what verification found missing, now in code.

Done when:

* **Never-transfer phrases** hold back a topic transfer on the sentence that
  names one -- and never an explicit request for a person or an emergency.
  A **vulnerable caller** topic exists, is opt-in, and is handled like an
  emergency (the 112 line). A policy field that does not exist is refused by
  name, never accepted and dropped.
* **Precision**: the labelled phrase set (``escalation_phrases``) reads as
  labelled, including the over-escalations verification found; and a rule
  in **shadow** mode records "would have escalated" on the call without
  acting.
* **India only**: an overseas number is refused with the reason, in the
  policy and, with the flag on, in the workspace's escalation number and a
  transfer tool's fixed destination.
* **The card** carries a team (from the topic -> team mapping, out-of-scope
  topics routed to theirs), links to the live view, and each person is
  briefed in their own language.
* **Measurement**: the turn an escalation fired on is stored with the call's
  outcome, a reviewer's label can be set later, and the tolerance-window
  report computes precision and recall from them.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api import constants
from api.db import db_client
from api.services.escalation import ReasonCode, card, fallbacks, measure, settings
from api.services.escalation.evaluator import Action, EscalationEvaluator
from api.services.escalation.policy import (
    BRIEFING_LANGUAGES,
    INDIA_ONLY,
    EscalationPolicy,
    TransferTarget,
    parse,
    validate_changes,
)
from api.services.escalation.signals import human_request, read
from api.tests.escalation_phrases import HUMAN_REQUEST, REGRESSIONS, TOPICS
from api.tests.support.voice import client_as
from api.tests.test_escalation_calls import (  # noqa: F401 - fixtures
    FIRST,
    SECOND,
    FakeEngine,
    FakeProvider,
    _outcome,
    _provider,
    _rows,
    _runtime,
    call,
    flag_on,
    people,
    quick,
)

TEAM_NUMBER = "+919800000009"


def _evaluator(available: bool = True, **policy) -> EscalationEvaluator:
    return EscalationEvaluator(
        EscalationPolicy(**policy), humans_available=lambda: available
    )


# --- 1. never-transfer, vulnerable callers, unknown fields -----------------------


def test_a_never_transfer_phrase_holds_back_a_topic_on_that_sentence():
    policy = dict(
        custom_topics=["cancel my membership"],
        never_transfer_topics=["store hours"],
        always_transfer_topics=["emergency", "legal_threat"],
    )
    held = _evaluator(**policy).observe_text(
        "What are your store hours? I might go to consumer court over this"
    )
    assert held.action == Action.NONE
    # The same topic without the phrase still goes to a person.
    assert (
        _evaluator(**policy).observe_text("I'll go to consumer court").action
        == Action.TRANSFER
    )
    # So does the owner's own phrase, unless the sentence names a never one.
    assert (
        _evaluator(**policy)
        .observe_text("store hours, and cancel my membership")
        .action
        == Action.NONE
    )


def test_a_never_transfer_phrase_never_holds_back_a_person_or_an_emergency():
    policy = dict(never_transfer_topics=["store hours", "delivery"])
    asked = _evaluator(**policy).observe_text(
        "Forget the store hours, let me talk to a human"
    )
    assert asked.action == Action.TRANSFER
    assert asked.reason == ReasonCode.EXPLICIT_REQUEST
    emergency = _evaluator(**policy).observe_text(
        "The delivery boy collapsed, he is bleeding, call an ambulance"
    )
    assert emergency.action == Action.TRANSFER and emergency.topic == "emergency"
    vulnerable = _evaluator(
        always_transfer_topics=["emergency", "vulnerable_caller"], **policy
    ).observe_text("I want to hurt myself, never mind the delivery")
    assert vulnerable.action == Action.TRANSFER
    assert vulnerable.topic == "vulnerable_caller"


def test_the_model_cannot_route_round_a_never_transfer_phrase():
    evaluator = _evaluator(never_transfer_topics=["refund status"])
    assert evaluator.observe_text("what is my refund status, it's a fraud").action == (
        Action.NONE
    )
    # The model reports the topic on the same turn: still held back.
    assert evaluator.observe_signal("policy_topic", topic="fraud").action == (
        Action.NONE
    )
    # A later sentence is a new turn.
    evaluator.observe_text("ok")
    assert evaluator.observe_signal("policy_topic", topic="fraud").action == (
        Action.TRANSFER
    )


def test_the_vulnerable_caller_topic_is_opt_in_and_handled_like_an_emergency():
    said = "I am 82 and live alone, I am confused about what I ordered"
    assert _evaluator().observe_text(said).action == Action.NONE
    chosen = _evaluator(
        always_transfer_topics=["emergency", "vulnerable_caller"]
    ).observe_text(said)
    assert chosen.action == Action.TRANSFER and chosen.reason == ReasonCode.POLICY
    assert chosen.topic == "vulnerable_caller"
    line = fallbacks.explanation(
        reached_nobody=True, outside_hours=False, reason="vulnerable_caller"
    )
    assert "112" in line
    assert "vulnerable_caller" in [t["key"] for t in settings.topics()]


def test_an_unknown_policy_field_is_refused_by_name_not_dropped():
    with pytest.raises(ValidationError) as caught:
        validate_changes({"never_transfer": ["billing"]})
    assert settings._message(caught.value) == (
        "never_transfer is not a setting this policy has."
    )
    with pytest.raises(ValidationError):
        validate_changes({"transfer_numbers": [{"number": FIRST, "dept": "x"}]})
    # Known now, and kept.
    kept = validate_changes({"never_transfer_topics": ["billing", "Billing", " "]})
    assert kept.never_transfer_topics == ["billing"]
    # A chat change carries unknown keys through to be refused, not lost.
    merged = settings.merged(EscalationPolicy(), {"weekend_numbers": []})
    assert "weekend_numbers" in merged
    with pytest.raises(ValidationError):
        validate_changes(merged)


def test_a_phrase_cannot_both_always_and_never_go_to_a_person():
    with pytest.raises(ValidationError, match="cannot both always and never"):
        validate_changes(
            {"custom_topics": ["Refund"], "never_transfer_topics": ["refund"]}
        )


def test_a_stored_policy_with_an_unknown_field_still_reads():
    policy = parse(
        {
            "always_transfer_topics": ["emergency"],
            "no_such_field": 1,
            "topic_teams": {"fraud": "Nobody"},
        }
    )
    assert policy.always_transfer_topics == ["emergency"]
    assert policy.topic_teams == {}


# --- 2. precision: the labelled phrases, and shadow mode ------------------------


@pytest.mark.parametrize("said, strength", HUMAN_REQUEST)
def test_labelled_requests_for_a_person(said, strength):
    assert human_request(said) == strength


@pytest.mark.parametrize("said, topic", TOPICS)
def test_labelled_topics(said, topic):
    found = [t for t in read(said).topics if t != "refund_over_limit"]
    assert found == ([topic] if topic else [])


@pytest.mark.parametrize("name", sorted(REGRESSIONS))
def test_what_verification_found_over_escalating_no_longer_does(name):
    evaluator = _evaluator()
    assert evaluator.observe_text(REGRESSIONS[name]).action == Action.NONE
    assert evaluator.decided is None


def test_an_accusation_is_read_as_one():
    reading = read("Aap log fraud ho, my cake never came")
    assert reading.accusation is True and "fraud" not in reading.topics


def test_a_rule_in_shadow_records_what_it_would_have_done_and_does_nothing():
    evaluator = _evaluator(rule_modes={"fraud": "shadow"})
    assert evaluator.observe_text("Hello, my order is late").action == Action.NONE
    decision = evaluator.observe_text("Someone used my card, I've been scammed")
    assert decision.action == Action.NONE and evaluator.decided is None
    assert evaluator.shadow_hits == [
        {
            "rule": "fraud",
            "reason_code": "policy",
            "topic": "fraud",
            "detail": "Topic: fraud",
            "would": "transfer",
            "caller_turn": 2,
        }
    ]
    # A rule that is on still acts, and nothing extra is recorded for it.
    asked = evaluator.observe_text("let me talk to a human")
    assert asked.action == Action.TRANSFER and asked.turn == 3
    assert len(evaluator.shadow_hits) == 1


def test_a_soft_signal_rule_in_shadow_neither_repairs_nor_transfers():
    evaluator = _evaluator(rule_modes={"repair_loop": "shadow"})
    actions = [evaluator.observe_signal("no_match").action for _ in range(5)]
    assert actions == [Action.NONE] * 5
    assert [h["rule"] for h in evaluator.shadow_hits][:1] == ["repair_loop"]
    assert all(h["would"] == "transfer" for h in evaluator.shadow_hits)


def test_frustration_in_shadow_does_not_lower_the_bar():
    on = _evaluator()
    on.observe_signal("frustration", level=3)
    shadowed = _evaluator(rule_modes={"frustration": "shadow"})
    shadowed.observe_signal("frustration", level=3)
    assert on.threshold() == shadowed.threshold() - 1
    seq = [shadowed.observe_signal("no_match").action for _ in range(3)]
    assert seq == [Action.NONE, Action.NONE, Action.REPAIR]


def test_emergencies_and_explicit_requests_cannot_run_in_shadow():
    for rule in ("emergency", "explicit_request"):
        with pytest.raises(ValidationError, match="cannot run in shadow"):
            validate_changes({"rule_modes": {rule: "shadow"}})
    with pytest.raises(ValidationError, match="not a rule"):
        validate_changes({"rule_modes": {"weather": "shadow"}})
    rules = {r["key"]: r["can_shadow"] for r in settings.rules()}
    assert rules["emergency"] is False and rules["fraud"] is True


def test_every_decision_carries_the_caller_turn():
    evaluator = _evaluator()
    for line in ("hi", "my order", "where is it"):
        evaluator.observe_text(line)
    assert evaluator.observe_text("connect me to a manager").turn == 4
    assert evaluator.caller_turns == 4


# --- 3. India only --------------------------------------------------------------------


@pytest.mark.parametrize(
    "number", ["+14155550123", "+44 20 7946 0958", "0044 7700 900123"]
)
def test_an_overseas_number_is_refused_with_the_reason(number):
    with pytest.raises(ValidationError) as caught:
        TransferTarget(number=number)
    assert INDIA_ONLY in str(caught.value)
    with pytest.raises(ValidationError) as caught:
        validate_changes({"transfer_numbers": [{"number": number}]})
    assert "Both legs of a call must be in India" in settings._message(caught.value)
    with pytest.raises(ValidationError):
        validate_changes(
            {"teams": [{"name": "Overseas", "numbers": [{"number": number}]}]}
        )


@pytest.mark.parametrize("number", ["98765 43210", "098765 43210", "+91 98765 43210"])
def test_an_indian_number_is_kept_in_one_form(number):
    assert TransferTarget(number=number).number == "+919876543210"


@pytest.mark.asyncio
async def test_the_workspace_escalation_number_is_india_only_with_the_flag(
    call, monkeypatch
):
    from api.services.voice import appointments

    monkeypatch.setattr(constants, "ESCALATION_V2_ENABLED", False)
    appointments._india_only(call.people.org, "+14155550123")  # unchanged off
    monkeypatch.setattr(constants, "ESCALATION_V2_ENABLED", True)
    with pytest.raises(appointments.PolicyInvalid, match="must be in India"):
        await appointments.save_policy(
            call.people.org,
            {"escalate_to": "+14155550123"},
            revision=0,
            user_id=call.people.a.id,
        )


def test_a_transfer_tool_number_is_india_only_with_the_flag(monkeypatch):
    from api.services.tool_management import (
        ToolManagementError,
        validate_transfer_destination,
    )

    def tool(destination: str, source: str = "static") -> dict:
        return {
            "type": "transfer_call",
            "config": {"destination": destination, "destination_source": source},
        }

    monkeypatch.setattr(constants, "ESCALATION_V2_ENABLED", False)
    validate_transfer_destination(tool("+14155550123"), organization_id=1)
    monkeypatch.setattr(constants, "ESCALATION_V2_ENABLED", True)
    with pytest.raises(ToolManagementError) as caught:
        validate_transfer_destination(tool("+14155550123"), organization_id=1)
    assert caught.value.status_code == 422 and "India" in caught.value.message
    # Templates, SIP endpoints and Indian numbers pass.
    for fine in ("{{initial_context.to}}", "PJSIP/1234", "+919876543210"):
        validate_transfer_destination(tool(fine), organization_id=1)
    validate_transfer_destination(tool("+14155550123", "dynamic"), organization_id=1)


# --- 4. the card: team, live link, the person's language -------------------------------


def test_topics_and_out_of_scope_subjects_route_to_teams():
    policy = validate_changes(
        {
            "teams": [{"name": "Billing", "numbers": [{"number": TEAM_NUMBER}]}],
            "topic_teams": {"fraud": "billing"},
            "out_of_scope_topics": [
                {"phrase": "home loans", "team": "Billing"},
                {"phrase": "job opening"},
            ],
        }
    )
    assert policy.team_for("fraud").name == "Billing"
    assert policy.team_for("legal_threat") is None
    decision = EscalationEvaluator(policy).observe_text("do you do home loans?")
    assert decision.topic == "out_of_scope" and decision.phrase == "home loans"
    assert policy.team_for(decision.topic, decision.phrase).name == "Billing"
    assert policy.team_for("out_of_scope", "job opening") is None
    with pytest.raises(ValidationError, match="not one of this agent's teams"):
        validate_changes({"topic_teams": {"fraud": "Nobody"}})
    with pytest.raises(ValidationError, match="not a topic"):
        validate_changes({"teams": [{"name": "A"}], "topic_teams": {"weather": "A"}})


def test_every_briefing_language_has_its_words():
    assert set(BRIEFING_LANGUAGES) <= set(card._BRIEFING_WORDS)
    with pytest.raises(ValidationError, match="not a briefing language"):
        TransferTarget(number=FIRST, language="ta")
    assert TransferTarget(number=FIRST, language="hi-IN").language == "hi"


@pytest.mark.asyncio
async def test_the_card_has_a_team_and_a_briefing_per_language():
    async def english(_t):
        return "Asha's order never came. She wants a person."

    async def in_language(_t, code):
        assert code == "hi"
        return "आशा का ऑर्डर नहीं आया। वह किसी व्यक्ति से बात करना चाहती हैं।"

    built = await card.build(
        escalation_uuid="e-9",
        workflow_id=7,
        workflow_run_id=9,
        reason_code="explicit_request",
        reason_detail="asked",
        call_context={},
        gathered={"customer_name": "Asha", "caller_verified": True},
        messages=[{"role": "user", "content": "Let me talk to a human."}],
        language="hi-IN",
        consent={},
        summarise=english,
        team="Billing",
        briefing_languages=["hi", "en", "hi"],
        summarise_in=in_language,
    )
    assert built["team"] == "Billing"
    assert set(built["briefings"]) == {"en", "hi"}
    assert card.spoken_briefing(built).startswith("Billing: Asha (verified).")
    hindi = card.spoken_briefing(built, "hi")
    assert hindi.startswith("Billing: Asha (सत्यापित)।")
    assert "किसी व्यक्ति से बात करना चाहते हैं" in hindi and "आशा" in hindi
    # A language with no briefing falls back to English, never to nothing.
    assert card.spoken_briefing(built, "ta").startswith("Billing: Asha")
    assert "Write it in Hindi" in card.summary_prompt("x", "hi")


@pytest.mark.asyncio
async def test_without_a_model_the_hindi_briefing_is_built_from_the_fields():
    built = await card.build(
        escalation_uuid="e-9",
        workflow_id=None,
        workflow_run_id=None,
        reason_code="policy",
        reason_detail="",
        call_context={},
        gathered={"intent": "refund"},
        messages=[{"role": "user", "content": "refund please"}],
        language=None,
        consent={},
        briefing_languages=["hi"],
    )
    assert built["briefings"]["hi"].startswith("एक कॉलर।")
    assert "मदद चाहिए" in built["briefings"]["hi"]
    assert built["transcript_url"] is None


def test_the_plivo_bridge_speaks_the_briefing_in_the_persons_language():
    import asyncio as _asyncio

    from api.services.telephony.providers.plivo.routes import (
        handle_plivo_transfer_bridge,
    )

    class Req:
        def __init__(self, params):
            self.query_params = params

    hindi = _asyncio.run(
        handle_plivo_transfer_bridge(
            "conf-1", Req({"briefing": "नमस्ते", "language": "hi"})
        )
    ).body.decode()
    assert '<Speak language="hi-IN" voice="Polly.Aditi">नमस्ते</Speak>' in hindi
    plain = _asyncio.run(
        handle_plivo_transfer_bridge(
            "conf-1", Req({"briefing": "Hello", "language": "xx"})
        )
    ).body.decode()
    assert "<Speak>Hello</Speak>" in plain


class HindiProvider(FakeProvider):
    BRIEFING_LANGUAGES = {"hi": ("hi-IN", "Polly.Aditi")}


@pytest.mark.asyncio
async def test_a_fraud_call_rings_its_team_first_and_briefs_each_in_their_language(
    call, monkeypatch, flag_on, quick
):
    provider = HindiProvider({TEAM_NUMBER: "no_answer", FIRST: "human"})
    _provider(monkeypatch, provider, call.run)
    engine = FakeEngine()
    runtime = _runtime(
        call,
        engine,
        transfer_numbers=[{"number": FIRST, "name": "Priya", "language": "en"}],
        teams=[
            {
                "name": "Fraud desk",
                "numbers": [{"number": TEAM_NUMBER, "language": "hi"}],
            }
        ],
        topic_teams={"fraud": "Fraud desk"},
    )
    runtime.on_user_text("Hello")
    runtime.on_user_text("Someone used my card, I've been scammed")
    await runtime._task

    assert [d[0] for d in provider.dialled] == [TEAM_NUMBER, FIRST]
    hindi_leg, english_leg = provider.dialled
    assert hindi_leg[2].get("briefing_language") == "hi"
    assert hindi_leg[1].startswith("Fraud desk: Asha। यह विषय हमेशा")
    assert "briefing_language" not in english_leg[2]
    assert english_leg[1].startswith("Fraud desk: Asha. Always goes to a person.")
    # The caller is told who is joining: the team, as nobody is named.
    assert "someone from our Fraud desk team" in " ".join(engine.spoken())

    [row] = await _rows(call.people.org, call.run.id)
    assert row.handoff_card["team"] == "Fraud desk"
    assert "?live=1&escalation=" in row.handoff_card["transcript_url"]
    outcome = await _outcome(call.people.org, call.run.id)
    assert outcome.caller_turn == 2 and outcome.caller_turns == 2


@pytest.mark.asyncio
async def test_a_carrier_that_cannot_speak_hindi_briefs_in_english(
    call, monkeypatch, flag_on, quick
):
    provider = FakeProvider({FIRST: "human"})
    _provider(monkeypatch, provider, call.run)
    runtime = _runtime(call, transfer_numbers=[{"number": FIRST, "language": "hi"}])
    runtime.on_user_text("Let me talk to a human")
    await runtime._task
    [(_, briefing, options)] = provider.dialled
    assert "briefing_language" not in options and briefing.startswith("Asha.")


@pytest.mark.asyncio
async def test_what_the_caller_says_on_hold_reaches_the_live_transcript(
    call, monkeypatch, flag_on, quick
):
    provider = FakeProvider({FIRST: "human"})
    _provider(monkeypatch, provider, call.run)
    runtime = _runtime(call)
    runtime.on_user_text("Let me talk to a human")
    await runtime._task
    assert runtime.row.state == "bridged"  # still open: a person has the caller
    runtime.on_user_text("hello? are you still there")
    await runtime.finalise()
    [row] = await _rows(call.people.org, call.run.id)
    assert row.handoff_card["live_transcript"][-1] == (
        "Caller: hello? are you still there"
    )


@pytest.mark.asyncio
async def test_a_shadow_hit_is_written_on_the_calls_outcome(call, flag_on):
    runtime = _runtime(call, rule_modes={"legal_threat": "shadow"})
    runtime.on_user_text("I want to order a cake")
    runtime.on_user_text("I'll send you a legal notice if it is late")
    assert runtime._task is None
    await runtime.finalise()
    outcome = await _outcome(call.people.org, call.run.id)
    assert outcome.outcome == "resolved_by_ai"
    assert outcome.caller_turn is None and outcome.caller_turns == 2
    [hit] = outcome.shadow_escalations
    assert hit["rule"] == "legal_threat" and hit["caller_turn"] == 2
    assert await _rows(call.people.org, call.run.id) == []


# --- 5. measurement -----------------------------------------------------------------


def _r(label, fired=None, expected=None, outcome=None) -> measure.Row:
    outcome = outcome or (
        "escalated"
        if label in ("escalated_correctly", "escalated_unnecessarily")
        else "resolved_by_ai"
    )
    return measure.Row(outcome, label, fired, expected)


def test_the_tolerance_window_counts_on_time_early_and_late():
    rows = [
        _r("escalated_correctly", 3, 3),  # on time
        _r("escalated_correctly", 4, 3),  # one late: inside a window of 1
        _r("escalated_correctly", 8, 3),  # five late: outside
        _r("escalated_correctly", 2, 3),  # early
        _r("escalated_correctly", 5),  # no turn named: a hit
        _r("escalated_unnecessarily", 2),
        _r("should_have_escalated", None, 4),
        _r("resolved_by_ai"),
        _r(None),
    ]
    one = measure.score(rows, window=1)
    assert (one["on_time"], one["early"], one["late"], one["untimed"]) == (3, 1, 1, 1)
    assert one["precision"] == round(3 / 6, 4)
    assert one["recall"] == round(3 / 6, 4)
    assert one["f1"] == 0.5
    assert one["labelled"] == 8 and one["unlabelled"] == 1
    assert one["labels"]["should_have_escalated"] == 1
    five = measure.score(rows, window=5)
    assert five["on_time"] == 4 and five["late"] == 0
    zero = measure.score(rows, window=0)
    assert zero["on_time"] == 2
    assert measure.score([], window=1)["precision"] is None


def test_a_label_that_cannot_be_true_is_refused():
    with pytest.raises(measure.LabelInvalid, match="does not fit"):
        measure.check_label("resolved_by_ai", "escalated_correctly", None, 4)
    with pytest.raises(measure.LabelInvalid, match="not a label"):
        measure.check_label("escalated", "great", None, 4)
    with pytest.raises(measure.LabelInvalid, match="Only escalated_correctly"):
        measure.check_label("escalated", "escalated_unnecessarily", 2, 4)
    with pytest.raises(measure.LabelInvalid, match="not one of them"):
        measure.check_label("resolved_by_ai", "should_have_escalated", 9, 4)
    measure.check_label("resolved_by_ai", "should_have_escalated", 3, 4)


@pytest.mark.asyncio
async def test_a_reviewer_labels_a_call_and_the_report_reads_it(
    call, monkeypatch, flag_on, quick
):
    provider = FakeProvider({FIRST: "human"})
    _provider(monkeypatch, provider, call.run)
    runtime = _runtime(call)
    for line in ("My cake is late", "It was for a birthday", "let me talk to a human"):
        runtime.on_user_text(line)
    await runtime._task
    outcome = await _outcome(call.people.org, call.run.id)
    assert outcome.caller_turn == 3

    url = f"/api/v1/escalations/outcomes/{call.run.id}/label"
    async with client_as(call.people.as_a) as client:
        wrong = await client.put(url, json={"label": "should_have_escalated"})
        assert wrong.status_code == 422 and "does not fit" in wrong.json()["detail"]
        unknown = await client.put(url, json={"label": "x", "verdict": "y"})
        assert unknown.status_code == 422
        labelled = await client.put(
            url, json={"label": "escalated_correctly", "expected_turn": 2}
        )
        assert labelled.status_code == 200
        assert labelled.json()["qa_label"] == "escalated_correctly"
        assert labelled.json()["caller_turn"] == 3
        late = (await client.get("/api/v1/escalations/report?window=0")).json()
        assert late["late"] == 1 and late["precision"] == 0.0
        fine = (await client.get("/api/v1/escalations/report?window=1")).json()
        assert fine["on_time"] == 1 and fine["precision"] == 1.0
        assert fine["recall"] == 1.0 and fine["calls"] == 1
    # A later write of the call's outcome does not wipe the label.
    await runtime._record_outcome()
    kept = await _outcome(call.people.org, call.run.id)
    assert kept.qa_label == "escalated_correctly" and kept.qa_expected_turn == 2

    async with client_as(call.people.as_c) as client:
        # Another workspace cannot label or read this call.
        assert (
            await client.put(url, json={"label": "escalated_unnecessarily"})
        ).status_code == 404
        report = (await client.get("/api/v1/escalations/report")).json()
        assert report["calls"] == 0


@pytest.mark.asyncio
async def test_the_policy_route_refuses_unknown_and_overseas_and_keeps_new_fields(
    call, flag_on
):
    url = f"/api/v1/escalations/policy/{call.workflow.id}"
    async with client_as(call.people.as_a) as client:
        dropped = await client.put(
            url, json={"policy": {"never_transfer": ["billing"]}}
        )
        assert dropped.status_code == 422
        assert dropped.json()["detail"] == (
            "never_transfer is not a setting this policy has."
        )
        overseas = await client.put(
            url, json={"policy": {"transfer_numbers": [{"number": "+14155550123"}]}}
        )
        assert overseas.status_code == 422
        assert "Both legs of a call must be in India" in overseas.json()["detail"]
        saved = await client.put(
            url,
            json={
                "policy": {
                    "never_transfer_topics": ["billing"],
                    "always_transfer_topics": ["emergency", "vulnerable_caller"],
                    "rule_modes": {"vulnerable_caller": "shadow"},
                    "transfer_numbers": [{"number": FIRST, "language": "hi"}],
                    "teams": [{"name": "Care", "numbers": [{"number": SECOND}]}],
                    "topic_teams": {"vulnerable_caller": "Care"},
                }
            },
        )
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["policy"]["never_transfer_topics"] == ["billing"]
        assert body["policy"]["transfer_numbers"][0]["language"] == "hi"
        assert body["policy"]["rule_modes"] == {"vulnerable_caller": "shadow"}
        assert {"key": "hi", "label": "Hindi"} in body["briefing_languages"]
        assert any(r["key"] == "frustration" for r in body["rules"])
        again = (await client.get(url)).json()
        assert again["policy"]["topic_teams"] == {"vulnerable_caller": "Care"}


@pytest.mark.asyncio
async def test_with_the_flag_off_the_new_routes_are_404(call, monkeypatch):
    monkeypatch.setattr(constants, "ESCALATION_V2_ENABLED", False)
    async with client_as(call.people.as_a) as client:
        assert (await client.get("/api/v1/escalations/report")).status_code == 404
        assert (
            await client.put(
                f"/api/v1/escalations/outcomes/{call.run.id}/label",
                json={"label": "resolved_by_ai"},
            )
        ).status_code == 404
    assert (
        await db_client.get_call_escalation_outcome(
            call.run.id, organization_id=call.people.org
        )
        is None
    )
