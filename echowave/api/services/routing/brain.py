"""Auto: the right Claude for each piece of work.

A workspace on Auto does not pick a model; each message, routine run or
Decibyl turn is sorted into one of three kinds of work and runs on the model
for that kind:

    quick  -> Everyday (Claude Haiku)   a greeting, a quick fact, one small action
    steps  -> Smart    (Claude Sonnet)  drafting, planning, tools, an attached file
    deep   -> Deep     (Claude Opus)    analysis, comparison, research, hard problems

Who sorts it is ``LAYA_ROUTING`` (constants): rules alone, Laya in shadow (its
answer logged beside the rules' and acted on by nobody), or Laya deciding with
the rules standing in whenever it is unsure, slow or down. The rules are never
removed: they are the floor Auto stands on when the decision model is not
there, and the baseline Laya has to beat.

Calls do not route per turn. A call keeps one model for its whole length --
switching mid-call loses the prompt cache and the model's own thinking -- so
a call on Auto runs on what Auto resolves to with no router: Everyday.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

from loguru import logger

from api import constants
from api.services.routing import decision

#: The three kinds of work, as the decision model is asked about them.
KINDS: dict[str, str] = {
    "quick": (
        "A short reply: a greeting, a simple question, a quick fact, a yes or "
        "no, or one small action."
    ),
    "steps": (
        "Needs several steps or tools: drafting a message or document, "
        "planning, scheduling, using connected apps, or working with a file."
    ),
    "deep": (
        "Needs careful reasoning: analysis, comparing options, research with "
        "sources, a long report, or a hard problem where mistakes are costly."
    ),
}
QUESTION = "What kind of work is this message asking for?"

#: Each kind's chat preset, and through it the managed tier it runs on.
PRESET_FOR = {"quick": "everyday", "steps": "smart", "deep": "deep"}

_DEEP_WORDS = re.compile(
    r"\b(analy[sz]e|analysis|compare|comparison|research|report|strategy|"
    r"evaluate|assess|investigate|audit|pros and cons|trade-?offs?|forecast|"
    r"root cause|in depth|in detail|deep dive|why does|why is)\b",
    re.IGNORECASE,
)
_STEPS_WORDS = re.compile(
    r"\b(draft|write|rewrite|plan|schedule|remind|send|email|mail|reply|"
    r"summari[sz]e|summary|book|create|update|follow[- ]?up|list|organi[sz]e|"
    r"translate|prepare|checklist|invoice|calendar|meeting)\b",
    re.IGNORECASE,
)


def by_rules(text: str, *, attachments: int = 0) -> str:
    """The kind of work by plain rules: length, attachments and a few words.

    Deliberately simple, and language-light: the length signals work in any
    script, and Laya is what is meant to read Hindi and Tamil well.
    """
    body = (text or "").strip()
    if not body:
        return "quick"
    lines = [line for line in body.splitlines() if line.strip()]
    questions = body.count("?")
    if len(body) > 1200 or (_DEEP_WORDS.search(body) and len(body) > 80):
        return "deep"
    if (
        attachments
        or len(body) > 280
        or len(lines) > 3
        or questions > 1
        or _STEPS_WORDS.search(body)
    ):
        return "steps"
    return "quick"


@dataclass(frozen=True)
class Route:
    kind: str
    preset: str
    #: rules, laya, or laya_fallback (Laya abstained; the rules decided).
    source: str
    confidence: float | None = None
    laya_kind: str | None = None
    laya_ms: int | None = None
    abstained: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_PENDING: set[asyncio.Task] = set()


def _count_shadow(laya_eval, ruled: str, asked: decision.Decision) -> None:
    """Agreement counters for the evaluation report, off the reply path."""
    from api.services import features

    if not features.is_on(laya_eval.GUARDRAILS_FLAG):
        return
    try:
        task = asyncio.get_running_loop().create_task(
            laya_eval.record_shadow(ruled, asked)
        )
    except RuntimeError:
        return
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


async def route(text: str, *, attachments: int = 0) -> Route:
    """Sort one piece of work. Never raises: the rules are always there."""
    from api.services.ops import laya_eval

    ruled = by_rules(text, attachments=attachments)
    mode = constants.LAYA_ROUTING
    if mode not in ("shadow", "on") or not decision.enabled():
        return Route(kind=ruled, preset=PRESET_FOR[ruled], source="rules")
    # The rollback switch (stream ops): rules alone, Laya never asked.
    if laya_eval.rolled_back():
        return Route(
            kind=ruled,
            preset=PRESET_FOR[ruled],
            source="rules",
            abstained="rolled_back",
        )

    # Behind the hard deadline and circuit breaker while laya_guardrails is
    # on; exactly decision.choose while it is off.
    asked = await laya_eval.guarded_choose(QUESTION, KINDS, text)
    _count_shadow(laya_eval, ruled, asked)
    if mode == "shadow":
        if asked.label is not None and asked.label != ruled:
            logger.info(
                "Auto (shadow): rules said {}, Laya said {} at {:.2f}",
                ruled,
                asked.label,
                asked.confidence or 0.0,
            )
        return Route(
            kind=ruled,
            preset=PRESET_FOR[ruled],
            source="rules",
            confidence=asked.confidence,
            laya_kind=asked.label,
            laya_ms=asked.elapsed_ms,
            abstained=asked.abstained,
        )
    if asked.label is None:
        return Route(
            kind=ruled,
            preset=PRESET_FOR[ruled],
            source="laya_fallback",
            confidence=asked.confidence,
            laya_ms=asked.elapsed_ms,
            abstained=asked.abstained,
        )
    return Route(
        kind=asked.label,
        preset=PRESET_FOR[asked.label],
        source="laya",
        confidence=asked.confidence,
        laya_kind=asked.label,
        laya_ms=asked.elapsed_ms,
    )


async def workspace_is_auto(organization_id: int | None) -> bool:
    """Whether this workspace's brain is Auto (the default for one that has
    not chosen), with no exact model pinned and no speech-to-speech bundle."""
    if organization_id is None:
        return False
    from api.services.configuration import managed_tiers
    from api.services.configuration.ai_model_configuration import (
        get_organization_ai_model_configuration_v2,
        managed_default_configuration,
    )

    stored = await get_organization_ai_model_configuration_v2(organization_id)
    if stored is None:
        stored = managed_default_configuration()
    if stored.mode != "decibyl" or stored.decibyl is None:
        return False
    managed = stored.decibyl
    if (managed.slots or {}).get("llm") or (managed.realtime_tier or "").strip():
        return False
    return (
        managed_tiers.canonical_tier("llm", managed.llm_tier)
        == managed_tiers.AUTO_LLM_TIER
    )


def agent_follows_workspace(workflow_configurations: dict | None) -> bool:
    """Whether an agent's brain is the workspace's (no brain of its own)."""
    from api.services.configuration.ai_model_configuration import (
        WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY,
        _is_managed_default,
        compile_workflow_model_configuration_override,
    )

    override = (workflow_configurations or {}).get(
        WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY
    )
    if not override:
        return True
    try:
        effective = compile_workflow_model_configuration_override(override)
    except Exception:  # noqa: BLE001 -- an unreadable override is its own brain
        return False
    if effective.is_realtime or effective.llm is None:
        return False
    return _is_managed_default(effective.llm)


async def auto_route(
    organization_id: int | None,
    text: str,
    *,
    workflow_configurations: dict | None = None,
    attachments: int = 0,
    feature: str = "",
    workflow_id: int | None = None,
    ref: str | None = None,
) -> Route | None:
    """The route for this work, or None when Auto is not in charge of it --
    the workspace pinned a model, or the agent has a brain of its own.

    ``feature`` names the caller (``text_chat``, ``decibyl``); with it, each
    decision is kept as training data for the router -- under the workspace's
    consent, redacted, and never in the way of the reply
    (``training_loop.hooks.routing_decision``). ``ref`` is what later
    outcomes link back by (``hooks.routing_ref``)."""
    try:
        if workflow_configurations is not None and not agent_follows_workspace(
            workflow_configurations
        ):
            return None
        if not await workspace_is_auto(organization_id):
            return None
        started = time.monotonic()
        routed = await route(text, attachments=attachments)
        if feature:
            from api.services.training_loop import hooks

            await hooks.routing_decision(
                organization_id=organization_id,
                workflow_id=workflow_id,
                feature=feature,
                ref=ref,
                text=text,
                attachments=attachments,
                routed=routed,
                candidates=PRESET_FOR,
                mode=constants.LAYA_ROUTING,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        return routed
    except Exception as exc:  # noqa: BLE001 -- routing must never cost a reply
        logger.warning("Auto routing skipped for org {}: {}", organization_id, exc)
        return None
