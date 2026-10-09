"""Simulated callers for escalation v2, τ-bench style (Yao et al., 2024).

Each **persona** has a goal and a script: what it says turn by turn, with
several paraphrases per turn, and -- where a real call would have them --
what the agent's model reports through ``report_escalation_signal`` on that
turn (a deterministic double for the model's judgement; the repo has no model
key in CI). Everything downstream is the real code: the evaluator, the record,
the handoff card, the ladder on a fake carrier, the outcome row.

**Scored on final state, not on text.** A trial passes when the database says
what the persona's goal says it should: whether there is an escalation, its
reason code, the card's fields, the turn it fired on -- or, for the persona
that must not escalate, that there is none and the call ended resolved by the
agent.

**Repeated trials with seeded variation.** Each persona runs ``TRIALS`` times;
each trial is seeded, picks one paraphrase per turn and adds the hesitations
callers really start with ("umm", "haan", "sorry"). τ-bench's pass^k: a suite
passes only if **every** trial passes, because an escalation rule that is
right four calls in five is wrong for the fifth caller.

One suite per trigger, as the plan asks: explicit request, fraud mention,
three failures, an angry code-mixed caller, and an ambiguous caller who must
NOT be handed over. Moved here from verification's scratch suite.

The caller is scripted, not a model. With a model key, a persona's script can
be swapped for a model playing the persona against the same goal and scorer.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from api.db import db_client
from api.enums import CallType
from api.services.escalation.policy import EscalationPolicy
from api.services.escalation.runtime import EscalationRuntime
from api.tests.escalation_phrases import REGRESSIONS
from api.tests.test_escalation_calls import (  # noqa: F401 - fixtures
    CALLER,
    FIRST,
    FakeEngine,
    FakeProvider,
    _outcome,
    _provider,
    _rows,
    call,
    flag_on,
    people,
    quick,
)

TRIALS = 5
FRAUD_DESK = "+919800000007"

_FILLERS = ("", "umm ", "haan ", "sorry, ", "ok ", "hello? ")
_TAILS = ("", "", " please", " ji", "...")


@dataclass
class Turn:
    """One caller turn: paraphrases, and what the model double reports."""

    says: list[str]
    #: Model-reported signals on this turn: (kind, {topic|level|step}).
    signals: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


@dataclass
class Persona:
    name: str
    goal: str
    turns: list[Turn]
    #: None: the call must end with no escalation at all.
    reason_code: str | None
    #: The caller turn the escalation must fire on (1-based).
    fires_on: int | None = None
    topic_team: str | None = None
    language: str = "en-IN"


def _vary(rng: random.Random, line: str) -> str:
    return f"{rng.choice(_FILLERS)}{line}{rng.choice(_TAILS)}"


EXPLICIT = Persona(
    name="explicit_request",
    goal="Gets a person as soon as they ask, with no repair attempt first.",
    turns=[
        Turn(["My order 1234 never came.", "Order 1234 nahi aaya abhi tak."]),
        Turn(
            [
                "Let me talk to a human",
                "I want a real person, not a machine",
                "Connect me to customer care",
                "mujhe kisi insaan se baat karni hai",
                "manager se baat karao abhi",
                "Can I speak to someone?",
            ]
        ),
    ],
    reason_code="explicit_request",
    fires_on=2,
)

FRAUD = Persona(
    name="fraud_mention",
    goal="A caller reporting fraud reaches the fraud desk at once.",
    turns=[
        Turn(["Hi, I have a problem with a payment.", "Payment ka issue hai."]),
        Turn(
            [
                "Someone used my card, I think I've been scammed",
                "I shared my OTP with a caller and money went",
                "mere saath dhokha hua hai, paise kat gaye",
                "There's an unauthorized transaction on my account",
                "I got a phishing message pretending to be you",
            ]
        ),
    ],
    reason_code="policy",
    fires_on=2,
    topic_team="Fraud desk",
)

THREE_FAILURES = Persona(
    name="three_failures",
    goal="The agent fails the same step three times: one repair, then a person.",
    turns=[
        Turn(
            ["My order number is 12-34-ABC", "order number one two three four"],
            [("step_failed", {"step": "verify_order"})],
        ),
        Turn(
            ["I said 1234", "one two three four, ABC"],
            [("step_failed", {"step": "verify_order"})],
        ),
        Turn(
            ["It's 1234!", "1234 hai, ABC"],
            [("step_failed", {"step": "verify_order"})],
        ),
    ],
    reason_code="repair_loop",
    fires_on=3,
)

ANGRY_CODE_MIXED = Persona(
    name="angry_code_mixed",
    goal=(
        "An angry Hinglish caller the agent keeps misunderstanding: anger alone "
        "never hands them over, but it makes the agent give up sooner."
    ),
    turns=[
        Turn(
            [
                "Yaar kitni baar bolun, my order still nahi aaya, this is ridiculous",
                "Bahut bura service hai, I have been waiting since Monday",
            ],
            [("frustration", {"level": 3})],
        ),
        Turn(
            [
                "Kya bakwaas hai, nobody picks up and the cake was wrong",
                "Aap log fraud ho, my cake never came",
            ],
            [("no_match", {})],
        ),
        Turn(
            ["Arre suno na, chocolate wala cake tha", "I told you, the chocolate one"],
            [("no_match", {})],
        ),
        Turn(
            ["Haan wahi, chocolate", "Chocolate! Kitni baar"],
            [("no_match", {})],
        ),
    ],
    reason_code="frustration",
    fires_on=4,
)

AMBIGUOUS = Persona(
    name="ambiguous_must_not_escalate",
    goal="A caller who mentions people and transfers but never asks for one is helped by the agent.",
    turns=[
        Turn(["Are you a robot?", "Is this customer care?", "kya aap insaan ho?"]),
        Turn(
            [
                REGRESSIONS["speak_to_someone_at_home"],
                "main ghar pe kisi se baat karke batata hoon",
            ]
        ),
        Turn([REGRESSIONS["transfer_me_the_refund"], "transfer me the money today"]),
        Turn(
            [
                REGRESSIONS["need_to_check_with_manager"],
                "mujhe apne manager se baat karni padegi",
            ]
        ),
        Turn(
            [
                REGRESSIONS["angry_you_are_fraud"],
                "you guys are a total scam, the order is late again",
            ]
        ),
        Turn(["OK, one chocolate cake please", "Theek hai, ek chocolate cake"]),
    ],
    reason_code=None,
)


# --- running one trial on the real runtime ------------------------------------------


class _Params:
    """What pipecat hands a function-call handler: arguments and a callback."""

    def __init__(self, arguments: dict[str, Any]):
        self.arguments = arguments
        self.results: list[Any] = []

    async def result_callback(self, result, properties=None):
        self.results.append(result)


def _engine_for(lines: list[str]) -> FakeEngine:
    engine = FakeEngine()
    engine._gathered_context = {
        "call_id": "CALL-1",
        "customer_name": "Asha Rao",
        "caller_verified": True,
        "order_id": "ORD-1234",
        "intent": "cake order never delivered",
    }
    messages: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "t1", "function": {"name": "lookup_order", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "t1", "content": '{"status": "dispatched"}'},
    ]
    messages += [{"role": "user", "content": line} for line in lines]
    engine.context = SimpleNamespace(get_messages=lambda: messages)
    return engine


async def _new_run(call, n: int):
    return await db_client.create_workflow_run(
        f"WR-SIM-{n}",
        call.workflow.id,
        "plivo",
        user_id=call.people.a.id,
        call_type=CallType.INBOUND,
        initial_context={"caller_number": CALLER},
        gathered_context={"call_id": "CALL-1"},
        organization_id=call.people.org,
    )


async def _trial(call, persona: Persona, seed: int) -> tuple[bool, str]:
    """Run one seeded trial; return (passed, why not)."""
    rng = random.Random(f"{persona.name}:{seed}")
    lines = [_vary(rng, rng.choice(turn.says)) for turn in persona.turns]
    run = await _new_run(call, seed + 100 * len(persona.name))
    engine = _engine_for(lines)
    runtime = EscalationRuntime(
        engine=engine,
        policy=EscalationPolicy(
            transfer_numbers=[{"number": FIRST, "name": "Priya"}],
            teams=[{"name": "Fraud desk", "numbers": [{"number": FRAUD_DESK}]}],
            topic_teams={"fraud": "Fraud desk"},
        ),
        organization_id=call.people.org,
        workflow_id=call.workflow.id,
        workflow_run=run,
        owner_user_id=call.people.a.id,
        language=persona.language,
    )
    for n, (turn, line) in enumerate(zip(persona.turns, lines), start=1):
        runtime.on_user_text(line)
        for kind, extra in turn.signals:
            await runtime._signal_handler(_Params({"kind": kind, **extra}))
        if runtime._task is not None and persona.fires_on and n < persona.fires_on:
            return False, f"escalated early, on turn {n}: {line!r}"
    if runtime._task is not None:
        await runtime._task
    await runtime.finalise()

    rows = await _rows(call.people.org, run.id)
    outcome = await _outcome(call.people.org, run.id)
    if persona.reason_code is None:
        if rows:
            return False, f"escalated ({rows[0].reason_code}) on {lines}"
        if outcome is None or outcome.outcome != "resolved_by_ai":
            return False, "the call was not recorded as resolved by the agent"
        return True, ""

    if len(rows) != 1:
        return False, f"{len(rows)} escalations on {lines}"
    row = rows[0]
    card = row.handoff_card or {}
    checks = {
        "reason_code": row.reason_code == persona.reason_code,
        "one dial": row.attempt_count >= 1,
        "caller": card.get("caller", {}).get("name") == "Asha Rao"
        and card.get("caller", {}).get("verified") is True,
        "intent": card.get("intent") == "cake order never delivered",
        "fields": card.get("fields", {}).get("order_id") == "ORD-1234",
        "actions": [a["tool"] for a in card.get("actions", [])] == ["lookup_order"],
        "summary": bool(card.get("summary")),
        "language": card.get("language") == persona.language,
        "consent": card.get("consent", {}).get("ai_disclosed") is True,
        "live link": f"/run/{run.id}?live=1" in (card.get("transcript_url") or ""),
        "team": card.get("team") == persona.topic_team,
        "turn": outcome is not None and outcome.caller_turn == persona.fires_on,
        "outcome": outcome is not None and outcome.outcome == "escalated",
    }
    failed = [name for name, ok in checks.items() if not ok]
    return (not failed), f"{failed} on {lines}"


async def _suite(call, persona: Persona) -> None:
    results = [await _trial(call, persona, seed) for seed in range(TRIALS)]
    failures = [why for ok, why in results if not ok]
    # pass^k: every trial.
    assert not failures, f"{persona.name}: {len(failures)}/{TRIALS} failed: {failures}"


@pytest.fixture
def carrier(monkeypatch):
    provider = FakeProvider({FIRST: "human", FRAUD_DESK: "human"})
    _provider(monkeypatch, provider)
    return provider


# --- the suites -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_request(call, carrier, flag_on, quick):
    await _suite(call, EXPLICIT)
    # Never a repair note before the handover: asking was enough.
    assert len(carrier.dialled) == TRIALS


@pytest.mark.asyncio
async def test_fraud_mention(call, carrier, flag_on, quick):
    await _suite(call, FRAUD)
    # Routed to the fraud desk every time.
    assert {d[0] for d in carrier.dialled} == {FRAUD_DESK}


@pytest.mark.asyncio
async def test_three_failures(call, carrier, flag_on, quick):
    await _suite(call, THREE_FAILURES)


@pytest.mark.asyncio
async def test_angry_code_mixed_caller(call, carrier, flag_on, quick):
    await _suite(call, ANGRY_CODE_MIXED)


@pytest.mark.asyncio
async def test_ambiguous_caller_must_not_escalate(call, carrier, flag_on, quick):
    await _suite(call, AMBIGUOUS)
    assert carrier.dialled == []


def test_trials_vary_with_the_seed_and_repeat_with_it():
    def lines(seed: int) -> list[str]:
        rng = random.Random(f"{EXPLICIT.name}:{seed}")
        return [_vary(rng, rng.choice(t.says)) for t in EXPLICIT.turns]

    assert lines(3) == lines(3)
    assert len({tuple(lines(s)) for s in range(TRIALS)}) > 1
