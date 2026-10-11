"""Counterfactual sampling: what would the next-best model have said?

Auto's router (``brain.py``) is trained on where it sent work and how that
turned out. It never sees the road not taken. For a small sample of calls
that are safe to repeat, this runs the next-best model as well, in the
background, and keeps its answer and cost as a ``routing_counterfactual``
learning event. Nobody sees the answer: it is never returned to a caller,
shown on a screen or acted on.

**Which calls.** Only a call that its caller declares, in so many words, is
*not* customer-facing, *is* idempotent and has *no* side effects (``Safety``,
with no defaults, so a caller must answer all three), and whose feature is on
``EXPLORABLE_FEATURES``: classifiers and extraction. Voice, chat replies and
anything that sends are never sampled, and are named in ``NEVER_FEATURES`` as
well as left off the allowlist, so adding one to the allowlist by mistake
still fails (``test_routing_data``). This is an allowlist on purpose: a
sample that wrongly *runs* sends a second message to a customer; a sample that
wrongly does not run costs a row of training data.

**What gates it, in order:** the ``routing_explore`` flag and ``training_loop``
for the workspace (both default off); the workspace's "Use my feedback to
improve my agents" setting (off, nothing runs and nothing is paid for); the
call's safety and feature; the sample rate (``SAMPLE_RATE_PERCENT``); and the
weekly learning cap. The sample's cost is counted against the same weekly
ceiling as every other learning spend (``training_loop.budget``): the smaller
of 2% of the agent's last four weeks of model spend and Rs 500. An agent-less
call counts against the workspace's.

The model call itself is the caller's (``runner``): this module decides
whether to run one and keeps the result. A runner that raises is logged and
costs nothing here.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from loguru import logger

from api.services import features, training_loop
from api.services.training_loop import budget, consent, record, summary

FLAG = "routing_explore"

#: The share of eligible calls that also get a counterfactual run, in percent.
SAMPLE_RATE_PERCENT = 1.0

#: Calls that are safe to repeat: they classify or extract, face nobody and
#: send nothing.
EXPLORABLE_FEATURES = frozenset({"classify", "extract"})

#: Never sampled, whatever else is declared. The allowlist above is what
#: decides; this is the second lock, and what a test holds the first against.
NEVER_FEATURES = frozenset(
    {
        "voice",
        "call",
        "text_chat",
        "decibyl",
        "chat_reply",
        "send",
        "send_message",
        "send_email",
        "whatsapp",
        "sms",
        "payment",
        "tool_call",
    }
)

#: Cheapest to dearest, as ``brain.PRESET_FOR`` names them.
LADDER = ("everyday", "smart", "deep")

#: How long a background sample may take before it is given up on.
RUN_TIMEOUT_SECONDS = 60

_PENDING: set[asyncio.Task] = set()


@dataclass(frozen=True)
class Safety:
    """What the caller says about its own call. No defaults: leaving one out
    is a type error, not a quiet yes."""

    customer_facing: bool
    idempotent: bool
    side_effects: bool


@dataclass(frozen=True)
class Sample:
    """What the alternative model produced."""

    output: str
    cost_paise: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    model: str | None = None


Runner = Callable[[str, str], Awaitable[Sample]]


def eligible(feature: str, safety: Safety) -> bool:
    if feature in NEVER_FEATURES or feature not in EXPLORABLE_FEATURES:
        return False
    return not safety.customer_facing and safety.idempotent and not safety.side_effects


def next_best(chosen_preset: str) -> str | None:
    """The model to compare against: the next one up the ladder, or the next
    one down from the top. None for a preset that is not on the ladder."""
    if chosen_preset not in LADDER:
        return None
    index = LADDER.index(chosen_preset)
    return LADDER[index + 1] if index + 1 < len(LADDER) else LADDER[index - 1]


async def _room(organization_id: int, workflow_id: int | None) -> bool:
    """Whether this week's learning cap has room: what the samples already
    cost this week is under the cap. A sample can overshoot by at most the one
    call that crossed it."""
    cap = await budget.weekly_cap_for_scope(
        organization_id=organization_id, workflow_id=workflow_id
    )
    if cap <= 0:
        return False
    from api.db import db_client

    spent = await db_client.routing_explore_spend_paise(
        organization_id=organization_id,
        workflow_id=workflow_id,
        since=summary.week_start(),
    )
    return spent < cap


async def maybe_sample(
    *,
    organization_id: int | None,
    workflow_id: int | None,
    feature: str,
    safety: Safety,
    text: str,
    chosen_preset: str,
    ref: str,
    runner: Runner,
    roll: Callable[[], float] = random.random,
) -> asyncio.Task | None:
    """Maybe start a counterfactual run in the background. Returns its task
    (for a caller or test that wants to wait), or None when nothing was
    started. Never raises, and never delays the real call by more than the
    gate's reads."""
    try:
        if not organization_id:
            return None
        if not (
            features.is_on(FLAG, organization_id)
            and training_loop.enabled(organization_id)
        ):
            return None
        if not eligible(feature, safety):
            return None
        alternative = next_best(chosen_preset)
        if alternative is None:
            return None
        if roll() * 100 >= SAMPLE_RATE_PERCENT:
            return None
        if not await consent.use_feedback(organization_id):
            return None
        if not await _room(organization_id, workflow_id):
            return None
        task = asyncio.get_running_loop().create_task(
            _run(
                organization_id=organization_id,
                workflow_id=workflow_id,
                feature=feature,
                text=text,
                chosen_preset=chosen_preset,
                alternative=alternative,
                ref=ref,
                runner=runner,
            )
        )
    except Exception as exc:  # noqa: BLE001 - sampling must never cost the call
        logger.warning(
            "routing_explore: not sampled for org {}: {}", organization_id, exc
        )
        return None
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)
    return task


async def _run(
    *,
    organization_id: int,
    workflow_id: int | None,
    feature: str,
    text: str,
    chosen_preset: str,
    alternative: str,
    ref: str,
    runner: Runner,
) -> bool:
    try:
        sample = await asyncio.wait_for(
            runner(alternative, text), timeout=RUN_TIMEOUT_SECONDS
        )
    except Exception as exc:  # noqa: BLE001 - background, and nobody is waiting
        logger.warning(
            "routing_explore: the alternative failed for org {}: {}",
            organization_id,
            exc,
        )
        return False
    return await record.record(
        organization_id=organization_id,
        event_type=training_loop.ROUTING_COUNTERFACTUAL,
        source=training_loop.ROUTING,
        subject_key=f"routing_cx:{feature}:{uuid.uuid4().hex}",
        workflow_id=workflow_id,
        scope=(
            training_loop.SCOPE_AGENT
            if workflow_id is not None
            else training_loop.SCOPE_DECIBYL
        ),
        input_ref=ref,
        input_text=text,
        model_output=sample.output,
        model=sample.model,
        prompt_tokens=sample.prompt_tokens,
        completion_tokens=sample.completion_tokens,
        data={
            "feature": feature,
            "chosen_preset": chosen_preset,
            "alternative_preset": alternative,
            "cost_paise": max(int(sample.cost_paise or 0), 0),
            "shown_to_user": False,
        },
    )
