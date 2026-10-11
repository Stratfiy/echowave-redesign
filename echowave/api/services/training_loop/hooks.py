"""Where the existing features tell the training loop what happened.

Each function here is the whole of one integration: what an edit card, an
action card, a thumb, a failed eval or an escalation says, turned into the
text a model would be trained on and handed to ``record``. The features
themselves call one function each and know nothing else about the loop.

None of these raise (``record`` never does, and building the text is wrapped
the same way): a card that settled must settle whether or not it was kept.

**What is never recorded.** A card that belongs to one person -- their
identity, care circle, meeting follow-ups, orders, forgetting a memory -- is
theirs, not the workspace's, and its words must not reach an export that the
workspace's owner reads. Action cards are therefore an allowlist
(``RECORDED_ACTIONS``): a new kind is not recorded until somebody decides it
should be. ``test_training_loop`` fails on a card kind that is in neither
list, so that decision cannot be skipped by accident.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import wraps
from typing import Any

from loguru import logger

from api.db import db_client
from api.services import training_loop as loop
from api.services.training_loop import consent, record

#: Action cards that are an agent's suggestion to the workspace, and so are
#: recorded. (Names as in ``services/workflow/actions.py``.)
RECORDED_ACTIONS = frozenset(
    {
        "turn_bot_on",
        "turn_bot_off",
        "return_missed_call",
        "create_bot",
        "build_from_spec",
        "install_from_repository",
        "schedule_routine",
        "track_commitment",
        "create_tracker",
        "set_up_booking",
        "run_tool",
    }
)

#: Action cards that are not, each for a reason that is about whose they are.
EXCLUDED_ACTIONS = frozenset(
    {
        # Forgetting is a person asking, not an agent suggesting, and the
        # card names what is being forgotten.
        "forget_fact",
        "forget_everything",
        # Bound to one person or their household.
        "care_family_invite",
        "care_family_share",
        "care_medicine_calls",
        "place_order",
        "run_outside_tool",
        "delete_learning_goal",
        "meeting_follow_up",
        "disconnect_app",
        "send_identity_email",
        "request_number",
        "send_document",
        "place_call",
        "call_when_done_number",
        # Steps on a person's own screen or computer.
        "desktop_step",
        "browser_step",
    }
)

#: Payload keys that mark a card as one person's.
_PRIVATE_KEYS = ("private_to", "only_user_id", "owner_user_id", "origin")

MAX_ARGS_CHARS = 4000


def never_raises(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - see the module docstring
            logger.warning("training_loop: {} failed: {}", fn.__name__, exc)
            return None

    return wrapper


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)


# --- edit cards -------------------------------------------------------------


def _edit_texts(payload: dict[str, Any]) -> tuple[str, str]:
    """What the agent was asked to improve, and the change it proposed."""
    step = _text(payload.get("step")) or "the agent"
    old = [_text(payload.get("old")).strip()] if payload.get("old") else []
    new = [_text(payload.get("new")).strip()] if payload.get("new") else []
    for greeting in payload.get("greetings") or []:
        if isinstance(greeting, dict):
            old.append("Greeting: " + _text(greeting.get("old")).strip())
            new.append("Greeting: " + _text(greeting.get("new")).strip())
    why = _text(payload.get("why")).strip()
    prompt = (
        f"Improve this part of an agent's instructions: {step}.\n\n"
        "Current text:\n" + "\n\n".join(old)
    )
    if why:
        prompt += f"\n\nReason for the change: {why}"
    return prompt, "\n\n".join(new)


def _edit_group(payload: dict[str, Any]) -> str:
    return f"edit_card:{payload.get('workflow_id')}:{_text(payload.get('step'))}"


@never_raises
async def edit_card_shown(
    *,
    organization_id: int | None,
    event_id: int | None,
    payload: dict[str, Any],
    workflow_run_id: int | None = None,
) -> None:
    if not organization_id or event_id is None:
        return
    prompt, output = _edit_texts(payload)
    await record.record(
        organization_id=organization_id,
        event_type=loop.SUGGESTION_SHOWN,
        source=loop.EDIT_CARD,
        subject_key=f"edit_card:{event_id}",
        workflow_id=payload.get("workflow_id"),
        workflow_run_id=workflow_run_id,
        input_ref=f"agent_event:{event_id}",
        group_key=_edit_group(payload),
        input_text=prompt,
        model_output=output,
    )


@never_raises
async def edit_card_settled(
    *,
    organization_id: int,
    event_id: int,
    payload: dict[str, Any],
    action: str,
    user_id: int | None,
    workflow_run_id: int | None = None,
) -> None:
    """Publish is the owner approving the card; Discard is the owner turning
    it down. A card the owner changed before publishing carries
    ``payload["original"]`` (the model's proposal, set by
    ``self_edit.settle``): that is ``edited_then_approved``, holding the
    model's text as ``model_output`` and the owner's as ``owner_final``, the
    two halves of a preference pair."""
    approved = action == "publish"
    await routing_outcome(
        organization_id=organization_id,
        ref=routing_ref(workflow_run_id, None),
        outcome_type=loop.APPROVED if approved else loop.REJECTED,
        outcome_ref=f"agent_event:{event_id}",
    )
    original = payload.get("original") if approved else None
    edited = isinstance(original, dict)
    prompt, output = _edit_texts({**payload, **original} if edited else payload)
    final = _edit_texts(payload)[1] if edited else output
    await record.record(
        organization_id=organization_id,
        event_type=(
            loop.EDITED_THEN_APPROVED
            if edited
            else (loop.APPROVED if approved else loop.REJECTED)
        ),
        source=loop.EDIT_CARD,
        subject_key=f"edit_card:{event_id}",
        workflow_id=payload.get("workflow_id"),
        user_id=user_id,
        input_ref=f"agent_event:{event_id}",
        group_key=_edit_group(payload),
        input_text=prompt,
        model_output=output,
        owner_final=final if approved else None,
    )


# --- action cards -----------------------------------------------------------


def action_recordable(payload: dict[str, Any]) -> bool:
    if payload.get("action") not in RECORDED_ACTIONS:
        return False
    return not any(payload.get(key) for key in _PRIVATE_KEYS)


def _action_texts(payload: dict[str, Any]) -> tuple[str, str]:
    label = _text(payload.get("label"))
    why = _text(payload.get("why")).strip()
    prompt = f"Propose an action for the owner to approve: {label}."
    if why:
        prompt += f"\n\nReason: {why}"
    args = _text(payload.get("args"))[:MAX_ARGS_CHARS]
    return prompt, f"{label}\n{args}".strip()


def _action_group(payload: dict[str, Any], workflow_id: int | None) -> str:
    return f"action_card:{workflow_id}:{payload.get('action')}"


@never_raises
async def action_card_shown(
    *,
    organization_id: int,
    event_id: int | None,
    workflow_id: int | None,
    workflow_run_id: int | None,
    payload: dict[str, Any],
) -> None:
    if event_id is None or not action_recordable(payload):
        return
    prompt, output = _action_texts(payload)
    await record.record(
        organization_id=organization_id,
        event_type=loop.SUGGESTION_SHOWN,
        source=loop.ACTION_CARD,
        subject_key=f"action_card:{event_id}",
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        input_ref=f"agent_event:{event_id}",
        group_key=_action_group(payload, workflow_id),
        input_text=prompt,
        model_output=output,
    )


async def _model_original(
    organization_id: int, event_id: int
) -> tuple[str | None, str | None]:
    """The words the model first proposed, if they were kept: what an owner's
    change is a change *from*. Not the previous revision, which may already be
    the owner's."""
    shown = await db_client.get_learning_event(
        organization_id=organization_id,
        event_type=loop.SUGGESTION_SHOWN,
        subject_key=f"action_card:{event_id}",
    )
    if shown is None:
        return None, None
    return shown.input_text, shown.model_output


@never_raises
async def action_card_confirmed(
    *,
    organization_id: int,
    event: Any,
    payload: dict[str, Any],
    user_id: int | None,
) -> None:
    """Confirm. If the owner changed the card first, it is
    ``edited_then_approved`` and carries both the model's version and theirs."""
    if not action_recordable(payload):
        return
    await routing_outcome(
        organization_id=organization_id,
        ref=routing_ref(event.workflow_run_id, None),
        outcome_type=loop.APPROVED,
        outcome_ref=f"agent_event:{event.id}",
    )
    prompt, final = _action_texts(payload)
    edited = bool(payload.get("revisions"))
    original_prompt, original_output = (
        await _model_original(organization_id, event.id) if edited else (None, None)
    )
    await record.record(
        organization_id=organization_id,
        event_type=loop.EDITED_THEN_APPROVED if edited else loop.APPROVED,
        source=loop.ACTION_CARD,
        subject_key=f"action_card:{event.id}",
        workflow_id=event.workflow_id,
        user_id=user_id,
        input_ref=f"agent_event:{event.id}",
        group_key=_action_group(payload, event.workflow_id),
        input_text=original_prompt or prompt,
        model_output=original_output if edited else final,
        owner_final=final,
    )


@never_raises
async def action_card_declined(
    *,
    organization_id: int,
    event: Any,
    payload: dict[str, Any],
    user_id: int | None,
) -> None:
    if not action_recordable(payload):
        return
    await routing_outcome(
        organization_id=organization_id,
        ref=routing_ref(event.workflow_run_id, None),
        outcome_type=loop.REJECTED,
        outcome_ref=f"agent_event:{event.id}",
    )
    prompt, output = _action_texts(payload)
    await record.record(
        organization_id=organization_id,
        event_type=loop.REJECTED,
        source=loop.ACTION_CARD,
        subject_key=f"action_card:{event.id}",
        workflow_id=event.workflow_id,
        user_id=user_id,
        input_ref=f"agent_event:{event.id}",
        group_key=_action_group(payload, event.workflow_id),
        input_text=prompt,
        model_output=output,
    )


@never_raises
async def action_card_undone(
    *,
    organization_id: int,
    event: Any,
    payload: dict[str, Any],
    user_id: int | None,
) -> None:
    if not action_recordable(payload):
        return
    prompt, output = _action_texts(payload)
    await record.record(
        organization_id=organization_id,
        event_type=loop.UNDONE,
        source=loop.ACTION_CARD,
        subject_key=f"action_card:{event.id}",
        workflow_id=event.workflow_id,
        user_id=user_id,
        input_ref=f"agent_event:{event.id}",
        group_key=_action_group(payload, event.workflow_id),
        input_text=prompt,
        model_output=output,
    )


@never_raises
async def action_card_revised(
    *,
    organization_id: int,
    event: Any,
    payload: dict[str, Any],
    user_id: int | None,
) -> None:
    """The owner changed what a waiting card would do: a correction, kept
    even if they never confirm it. Each revision is its own event; the model's
    original is the one the card was first shown with."""
    if not action_recordable(payload):
        return
    prompt, final = _action_texts(payload)
    original_prompt, original_output = await _model_original(organization_id, event.id)
    await record.record(
        organization_id=organization_id,
        event_type=loop.OWNER_CORRECTION,
        source=loop.ACTION_CARD,
        subject_key=(f"action_card:{event.id}:r{len(payload.get('revisions') or [])}"),
        workflow_id=event.workflow_id,
        user_id=user_id,
        input_ref=f"agent_event:{event.id}",
        group_key=_action_group(payload, event.workflow_id),
        input_text=original_prompt or prompt,
        model_output=original_output,
        owner_final=final,
    )


# --- thumbs ----------------------------------------------------------------


@never_raises
async def reply_thumb(
    *,
    organization_id: int,
    user_id: int,
    reply_event_id: int,
    workflow_id: int | None,
    folder_id: int | None = None,
    verdict: str,
    reasons: list[str],
    reply_text: str | None,
    model: str | None,
    prompt_text: str | None = None,
    workflow_run_id: int | None = None,
    thread_id: str | None = None,
) -> None:
    """Yes / Not quite on a reply: an agent's (scope ``agent``, with the
    agent), or one on Decibyl's own thread (scope ``decibyl``, no agent: a
    reply with neither an agent nor a folder). The same consent check and
    redaction either way (``record.record``). A reply in a folder is neither,
    and is not recorded."""
    if workflow_id is None and folder_id is not None:
        return
    scope = loop.SCOPE_AGENT if workflow_id is not None else loop.SCOPE_DECIBYL
    up = verdict == "yes"
    await record.record(
        organization_id=organization_id,
        event_type=loop.THUMBS_UP if up else loop.THUMBS_DOWN,
        source=loop.REPLY,
        subject_key=f"reply:{reply_event_id}:{user_id}:{verdict}",
        workflow_id=workflow_id,
        user_id=user_id,
        input_ref=f"agent_event:{reply_event_id}",
        input_text=prompt_text,
        model_output=reply_text,
        detail=",".join(reasons),
        model=model,
        scope=scope,
    )
    await routing_outcome(
        organization_id=organization_id,
        ref=routing_ref(workflow_run_id, thread_id, decibyl=workflow_id is None),
        outcome_type=loop.THUMBS_UP if up else loop.THUMBS_DOWN,
        outcome_ref=f"agent_event:{reply_event_id}",
    )


# --- evals ------------------------------------------------------------------


@never_raises
async def eval_finished(result_id: int) -> None:
    """A case an agent did not pass. Passes are not events here: they are not
    something to learn from, and the eval screen already keeps them."""
    from api.db.models import EvalCaseModel, EvalResultModel

    async with db_client.async_session() as session:
        result = await session.get(EvalResultModel, result_id)
        if result is None:
            return
        if result.status in ("passed", "failed"):
            await routing_outcome(
                organization_id=result.organization_id,
                ref=routing_ref(result.workflow_run_id, None),
                outcome_type="eval_pass"
                if result.status == "passed"
                else loop.EVAL_FAIL,
                outcome_ref=f"eval_result:{result_id}",
            )
        if result.status != "failed":
            return
        case = await session.get(EvalCaseModel, result.case_id)
        if case is None:
            return
        organization_id = result.organization_id
        # The case and the result must belong to one workspace, or neither is
        # read as evidence about the other.
        if case.organization_id != organization_id:
            return
        transcript = list(result.transcript or [])
        verdict = result.verdict
        run_id = result.workflow_run_id
        workflow_id = result.workflow_id
        case_id = case.id
        prompt = (
            "A caller is simulated against this agent.\n"
            f"Caller: {case.persona}\nGoal: {case.goal}\n"
            f"The agent must say: {', '.join(map(str, case.must_say or [])) or '-'}\n"
            f"The agent must not say: "
            f"{', '.join(map(str, case.must_not_say or [])) or '-'}"
        )
    agent_lines = [
        f"Agent: {t.get('text')}"
        for t in transcript
        if isinstance(t, dict) and t.get("role") == "agent" and t.get("text")
    ]
    await record.record(
        organization_id=organization_id,
        event_type=loop.EVAL_FAIL,
        source=loop.EVAL,
        subject_key=f"eval_result:{result_id}",
        workflow_id=workflow_id,
        workflow_run_id=run_id,
        input_ref=f"eval_result:{result_id}",
        group_key=f"eval_case:{case_id}",
        input_text=prompt,
        model_output="\n".join(agent_lines),
        detail=verdict,
    )


# --- escalations ------------------------------------------------------------


@never_raises
async def escalation_opened(
    *,
    organization_id: int,
    escalation_id: int,
    workflow_id: int | None,
    workflow_run_id: int | None,
    reason: str,
    detail: str | None,
) -> None:
    await record.record(
        organization_id=organization_id,
        event_type=loop.ESCALATION,
        source=loop.ESCALATION_SOURCE,
        subject_key=f"escalation:{escalation_id}",
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        input_ref=f"escalation:{escalation_id}",
        input_text=(
            f"The agent handed a caller to a person. Reason: {reason}. {detail or ''}"
        ).strip(),
        detail=reason,
    )


# --- routing ----------------------------------------------------------------


def routing_ref(
    workflow_run_id: int | None, thread_id: str | None, *, decibyl: bool = False
) -> str | None:
    """What links a routing decision to the outcomes that follow it: the run
    for an agent's chat, the thread for Decibyl's own. A thread's outcomes
    attach to its latest decision, which is the turn they were given on."""
    if workflow_run_id:
        return f"workflow_run:{workflow_run_id}"
    if decibyl:
        return f"decibyl_thread:{thread_id or 'main'}"
    return None


@never_raises
async def routing_decision(
    *,
    organization_id: int | None,
    workflow_id: int | None,
    feature: str,
    ref: str | None,
    text: str,
    attachments: int,
    routed: Any,
    candidates: dict[str, str],
    mode: str,
    latency_ms: int,
) -> None:
    """Auto sent one piece of work to a model. Recorded with what the router
    saw (the text redacted like any other, and its shape), the models it
    could have chosen, the one it did and who chose it (rules, Laya, or the
    rules standing in for Laya), Laya's own answer and confidence where it
    was asked (always, in shadow), and how long deciding took. Tokens, cost
    and the outcome arrive later (``routing_outcome``)."""
    if not organization_id:
        return
    body = text or ""
    laya = (
        {
            "kind": routed.laya_kind,
            "confidence": routed.confidence,
            "ms": routed.laya_ms,
            "abstained": routed.abstained,
        }
        if routed.laya_ms is not None or routed.laya_kind or routed.abstained
        else None
    )
    await record.record(
        organization_id=organization_id,
        event_type=loop.ROUTING_DECISION,
        source=loop.ROUTING,
        subject_key=f"routing:{feature}:{uuid.uuid4().hex}",
        workflow_id=workflow_id,
        scope=loop.SCOPE_AGENT if workflow_id is not None else loop.SCOPE_DECIBYL,
        input_ref=ref,
        input_text=body,
        data={
            "feature": feature,
            "mode": mode,
            "input": {
                "chars": len(body),
                "lines": len([x for x in body.splitlines() if x.strip()]),
                "questions": body.count("?"),
                "attachments": attachments,
            },
            "candidates": [
                {"kind": kind, "preset": preset} for kind, preset in candidates.items()
            ],
            "chosen": {
                "kind": routed.kind,
                "preset": routed.preset,
                "source": routed.source,
            },
            "laya": laya,
            "latency_ms": latency_ms,
            "cost_paise": None,
            "outcomes": [],
        },
    )


@never_raises
async def routing_outcome(
    *,
    organization_id: int | None,
    ref: str | None,
    outcome_type: str,
    outcome_ref: str | None = None,
) -> None:
    """Something happened to the work a routing decision sent to a model: an
    eval passed or failed, a person gave a thumb, a card was approved or
    turned down. Linked to the decision for ``ref``, together with the model,
    tokens and cost the run turned out to use. Nothing is written when the
    workspace has the loop or its setting off, or when there was no decision
    to link to."""
    if not organization_id or not ref or not loop.enabled(organization_id):
        return
    if not await consent.use_feedback(organization_id):
        return
    usage: dict[str, Any] | None = None
    if ref.startswith("workflow_run:"):
        run_id = int(ref.partition(":")[2])
        model, prompt_tokens, completion_tokens = await record.run_usage(
            run_id, organization_id
        )
        usage = {
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_paise": await db_client.run_model_cost_paise(
                organization_id=organization_id, workflow_run_id=run_id
            ),
        }
    await db_client.link_routing_outcome(
        organization_id=organization_id,
        input_ref=ref,
        outcome={
            "type": outcome_type,
            "ref": outcome_ref,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
        usage=usage,
    )
