"""The experience ledger: what was tried, with what, and what the records say.

Plan section 7: "Capture a task attempt, tool version, observed result,
correction, and outcome evidence." And the rule that governs all of it: "a
model saying 'I successfully booked it' is not evidence. Confirm bookings via
persisted records/provider receipts."

So a record's ``outcome`` is not a field anybody sets. It is read off its
``evidence``, and evidence is a list of pointers to rows something other
than the model wrote:

* ``app_interaction`` -- the provider's own reply to a tool call
  (``app_interactions.status``), success or error;
* ``call`` -- the carrier's answer time on the run. Answered is *not* a
  success: a picked-up phone is not a finished task;
* ``card`` -- a person's press on a card: discarded, declined or edited
  before confirming is a correction;
* ``message`` -- a person's own message saying how it should have been done;
* ``run`` -- the run itself, which proves only that it happened.

``gathered_context`` -- what the model extracted or claimed during the
conversation -- is never read here.

Content-minimised (plan section 9): names, statuses, ids and a one-line
correction. No transcript, no tool arguments, no message bodies: those stay
behind their own access controls and are fetched by reference when a lesson
is drafted.

Idempotent: unique by ``(organization_id, run_key, kind)``, so a run
collected twice, or by two workers at once, is one record.

Frozen split: whether a record may teach (``train``) or may only test
(``holdout``) is a hash of its identity, decided at insert and never changed.
A lesson is never tested on the cases that created it.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services import evolve

#: One record in this many is held out. Small enough to leave most of a small
#: workspace's experience to learn from, large enough that a family with a
#: dozen records has a few to test on.
HOLDOUT_EVERY = 4

TRAIN = "train"
HOLDOUT = "holdout"

SCOPE_WORKSPACE = "workspace"
SCOPE_PERSONAL = "personal"

#: Where evidence may point. Anything else is refused, not stored: an
#: evidence source nobody defined is a model's claim wearing a label.
SOURCES = frozenset({"app_interaction", "call", "card", "message", "run"})
SUCCESS_STATES = frozenset({"success", "delivered", "booked", "confirmed", "done"})
FAILURE_STATES = frozenset(
    {"error", "failed", "declined", "discarded", "edited", "correction"}
)

MAX_INSTRUCTION = 300

SUCCESS = "success"
FAILURE = "failure"
UNKNOWN = "unknown"


def split_for(organization_id: int, run_key: str) -> str:
    """Train or holdout, from the record's identity alone. The same record
    lands on the same side every time it is computed."""
    digest = hashlib.sha256(f"{organization_id}|{run_key}".encode()).hexdigest()
    return HOLDOUT if int(digest[:8], 16) % HOLDOUT_EVERY == 0 else TRAIN


def outcome_from(evidence: Iterable[dict[str, Any]]) -> str:
    """Failure if any evidence says so, success if any says that, and
    unknown otherwise -- which is most calls, and is honest."""
    states = {str((e or {}).get("state") or "").lower() for e in evidence or []}
    if states & FAILURE_STATES:
        return FAILURE
    if states & SUCCESS_STATES:
        return SUCCESS
    return UNKNOWN


def _clean_evidence(evidence: Iterable[Any]) -> list[dict[str, str]]:
    out = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "")
        ref = str(item.get("ref") or "")[:80]
        if source not in SOURCES or not ref:
            logger.warning("evolve: evidence from {!r} refused", source)
            continue
        out.append(
            {"source": source, "ref": ref, "state": str(item.get("state") or "")[:24]}
        )
    return out


def _clean_tools(tool_calls: Iterable[Any]) -> list[dict[str, Any]]:
    out = []
    for call in tool_calls or []:
        get = (
            call.get
            if isinstance(call, dict)
            else lambda k, c=call: getattr(c, k, None)
        )
        name = get("name")
        if not name:
            continue
        out.append(
            {
                "kind": str(get("kind") or "")[:64],
                "name": str(name)[:120],
                "app": str(get("app") or "")[:64] or None,
                "status": str(get("status") or "")[:24],
                "definition_id": get("definition_id"),
            }
        )
    return out[:40]


def clean_instruction(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_INSTRUCTION]


async def record(
    *,
    organization_id: int,
    run_key: str,
    kind: str,
    task_family: str,
    evidence: Iterable[dict[str, Any]],
    tool_calls: Iterable[Any] = (),
    user_id: int | None = None,
    scope: str = SCOPE_WORKSPACE,
    workflow_id: int | None = None,
    workflow_run_id: int | None = None,
    skill_slug: str | None = None,
    skill_version: int | None = None,
    correction: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> tuple[int, bool] | None:
    """Write one record; ``(id, created)``, or None when nothing was written
    (the flag is off, the kind is unknown, or there is no evidence)."""
    if not evolve.enabled(organization_id):
        return None
    if kind not in evolve.KINDS:
        logger.warning("evolve: unknown experience kind {!r}", kind)
        return None
    cleaned = _clean_evidence(evidence)
    if not cleaned:
        # No pointer to a persisted row, no record. This is the line that
        # keeps a model's account of itself out of the ledger.
        return None
    if scope not in (SCOPE_WORKSPACE, SCOPE_PERSONAL):
        scope = SCOPE_WORKSPACE
    if scope == SCOPE_PERSONAL and user_id is None:
        return None
    if correction is not None:
        correction = {
            "category": str(correction.get("category") or "instruction")[:40],
            "instruction": clean_instruction(correction.get("instruction")),
            "ref": str(correction.get("ref") or "")[:80],
        }
    return await db_client.insert_experience(
        organization_id=organization_id,
        run_key=str(run_key)[:64],
        kind=kind,
        task_family=str(task_family)[:96],
        evidence=cleaned,
        tool_calls=_clean_tools(tool_calls),
        outcome=outcome_from(cleaned),
        user_id=user_id,
        scope=scope,
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        skill_slug=skill_slug,
        skill_version=skill_version,
        correction=correction,
        split=split_for(organization_id, run_key),
        occurred_at=occurred_at or datetime.now(UTC),
    )


def family_for(slug: str | None, workflow_id: int | None) -> str:
    if slug:
        return slug
    if workflow_id is not None:
        return f"agent:{workflow_id}"
    return "decibyl"


async def _skills_on(organization_id: int, workflow_id: int | None) -> list[str]:
    if workflow_id is None:
        return []
    from api.services.skills import shelf

    try:
        return await shelf.for_workflow(organization_id, workflow_id)
    except Exception as exc:  # noqa: BLE001 - one reading of several
        logger.warning("evolve: could not read skills on {}: {}", workflow_id, exc)
        return []


async def collect_run(organization_id: int, workflow_run_id: int) -> int:
    """Record one finished run: one attempt per skill the agent carried
    (or one for the agent when it carried none). Returns how many records
    were new. Never raises."""
    if not evolve.enabled(organization_id):
        return 0
    try:
        owner = await db_client.get_organization_id_by_workflow_run_id(workflow_run_id)
        if owner != organization_id:
            return 0
        run = await db_client.get_workflow_run(
            workflow_run_id, organization_id=organization_id
        )
        if run is None or not getattr(run, "is_completed", False):
            return 0
        from api.services.workflow import answered as answered_rule
        from api.services.workflow import test_runs

        if test_runs.is_test(run):
            return 0
        interactions = await db_client.app_interactions_for_run(workflow_run_id)
        evidence: list[dict[str, Any]] = [
            {"source": "run", "ref": f"workflow_runs:{run.id}", "state": "finished"}
        ]
        for row in interactions:
            evidence.append(
                {
                    "source": "app_interaction",
                    "ref": f"app_interactions:{row.id}",
                    "state": str(row.status or ""),
                }
            )
        if (run.mode or "") != "textchat":
            evidence.append(
                {
                    "source": "call",
                    "ref": f"workflow_runs:{run.id}",
                    "state": "answered"
                    if answered_rule.was_answered(run)
                    else "not_answered",
                }
            )
        at = getattr(run, "created_at", None) or datetime.now(UTC)
        slugs = await _skills_on(organization_id, run.workflow_id)
        written = 0
        for slug in slugs or [None]:
            version = None
            if slug:
                active = await db_client.skill_version_active_at(
                    organization_id=organization_id, slug=slug, at=at
                )
                version = active.version if active is not None else None
            result = await record(
                organization_id=organization_id,
                run_key=f"run:{run.id}:{slug}" if slug else f"run:{run.id}",
                kind=evolve.ATTEMPT,
                task_family=family_for(slug, run.workflow_id),
                evidence=evidence,
                tool_calls=interactions,
                workflow_id=run.workflow_id,
                workflow_run_id=run.id,
                skill_slug=slug,
                skill_version=version,
                occurred_at=at,
            )
            if result and result[1]:
                written += 1
        return written
    except Exception as exc:  # noqa: BLE001 - offline bookkeeping
        logger.warning("evolve: could not collect run {}: {}", workflow_run_id, exc)
        return 0


async def collect_cards(organization_id: int, *, limit: int = 200) -> int:
    """Corrections a person made on cards: an edit discarded, an action
    declined or edited before it ran, a learned lesson turned down. Read off
    the cards' own stamped state. Returns how many records were new."""
    if not evolve.enabled(organization_id):
        return 0
    written = 0
    try:
        rows = await db_client.agent_events(
            organization_id=organization_id,
            kinds=[
                AgentEventKind.EDIT_PROPOSED.value,
                AgentEventKind.ACTION_PROPOSED.value,
                AgentEventKind.SKILL_LESSON.value,
            ],
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001 - offline bookkeeping
        logger.warning("evolve: could not read cards for {}: {}", organization_id, exc)
        return 0
    for row in rows:
        try:
            found = await _card_record(organization_id, row)
        except Exception as exc:  # noqa: BLE001 - one card is not the rest
            logger.warning("evolve: card {} not recorded: {}", row.id, exc)
            continue
        written += 1 if found else 0
    return written


async def _card_record(organization_id: int, row: Any) -> bool:
    payload = dict(row.payload or {})
    kind = row.kind
    workflow_id = row.workflow_id or payload.get("workflow_id")
    state = ""
    category = ""
    instruction = ""
    who = None
    if kind == AgentEventKind.EDIT_PROPOSED.value:
        decided = payload.get("decided") or {}
        if decided.get("action") != "discard":
            return False
        state, category = "discarded", "rejected_edit"
        instruction = (
            f"Turned down: {payload.get('why') or payload.get('step') or 'a change'}"
        )
        who = decided.get("by")
    elif kind == AgentEventKind.ACTION_PROPOSED.value:
        if payload.get("state") == "declined":
            state, category = "declined", "declined_action"
        elif payload.get("revisions"):
            state, category = "edited", "edited_action"
        else:
            return False
        instruction = (
            f"{'Declined' if state == 'declined' else 'Changed before confirming'}: "
            f"{payload.get('action') or 'an action'}"
        )
        who = (payload.get("revisions") or [{}])[-1].get("by") or payload.get(
            "requested_by"
        )
    elif kind == AgentEventKind.SKILL_LESSON.value:
        if payload.get("type") != "offer":
            return False
        decided = payload.get("decided") or {}
        if decided.get("action") != "discard":
            return False
        state, category = "discarded", "rejected_lesson"
        instruction = "Turned down a learned lesson: " + "; ".join(
            str(c.get("text") or "") for c in (payload.get("changes") or [])[:2]
        )
        who = decided.get("by")
    else:
        return False

    slug = payload.get("slug")
    if not slug and workflow_id is not None:
        on = await _skills_on(organization_id, int(workflow_id))
        slug = on[0] if len(on) == 1 else None
    private = payload.get("private_to")
    result = await record(
        organization_id=organization_id,
        run_key=f"event:{row.id}",
        kind=evolve.REJECTED_CARD,
        task_family=family_for(slug, int(workflow_id) if workflow_id else None),
        evidence=[{"source": "card", "ref": f"agent_events:{row.id}", "state": state}],
        user_id=int(private) if private else (int(who) if who else None),
        scope=SCOPE_PERSONAL if private else SCOPE_WORKSPACE,
        workflow_id=int(workflow_id) if workflow_id else None,
        skill_slug=slug,
        correction={
            "category": category,
            "instruction": instruction,
            "ref": f"agent_events:{row.id}",
        },
        occurred_at=getattr(row, "at", None),
    )
    return bool(result and result[1])


async def note_correction(
    *,
    organization_id: int,
    user_id: int,
    instruction: str,
    skill_slug: str | None = None,
    workflow_id: int | None = None,
    thread_id: str | None = None,
    category: str = "instruction",
) -> dict[str, Any]:
    """ "No, do it like this": record a person's correction.

    The evidence is the person's own message -- the newest one they wrote on
    this thread -- not the model's paraphrase of it. No such message, no
    record: a correction nobody typed is not a correction.
    """
    if not evolve.enabled(organization_id):
        return {"status": "unavailable", "reason": "not switched on here"}
    text = clean_instruction(instruction)
    if not text:
        return {"status": "not_recorded", "reason": "Say what should change."}
    rows = await db_client.agent_events(
        organization_id=organization_id,
        workflow_id=workflow_id,
        kinds=[AgentEventKind.MESSAGE.value],
        assistant_thread=workflow_id is None,
        thread_id=thread_id,
        viewer_id=user_id,
        limit=20,
    )
    said = next(
        (
            r
            for r in rows
            if r.actor == AgentEventActor.HUMAN.value
            and (r.payload or {}).get("author_id") in (user_id, str(user_id))
        ),
        None,
    )
    if said is None:
        return {
            "status": "not_recorded",
            "reason": "There is no message from the person on this thread to learn from.",
        }
    personal = workflow_id is None
    result = await record(
        organization_id=organization_id,
        run_key=f"event:{said.id}",
        kind=evolve.CORRECTION,
        task_family=family_for(skill_slug, workflow_id),
        evidence=[
            {
                "source": "message",
                "ref": f"agent_events:{said.id}",
                "state": "correction",
            }
        ],
        user_id=user_id,
        scope=SCOPE_PERSONAL if personal else SCOPE_WORKSPACE,
        workflow_id=workflow_id,
        skill_slug=skill_slug,
        correction={
            "category": category,
            "instruction": text,
            "ref": f"agent_events:{said.id}",
        },
        occurred_at=getattr(said, "at", None),
    )
    if not result:
        return {"status": "not_recorded", "reason": "Nothing was recorded."}
    return {"status": "recorded", "record_id": result[0], "new": result[1]}


def summary(row: Any) -> str:
    """One line for a card's evidence list. Names and states only."""
    when = getattr(row, "occurred_at", None)
    day = when.strftime("%d %b") if when else ""
    correction = getattr(row, "correction", None) or {}
    if correction.get("instruction"):
        line = correction["instruction"]
    else:
        failed = [
            t.get("name") for t in (row.tool_calls or []) if t.get("status") == "error"
        ]
        if failed:
            line = f"{', '.join(failed[:2])} failed"
        else:
            line = {
                SUCCESS: "Done, confirmed by the app's reply",
                FAILURE: "Did not work",
            }.get(row.outcome, "Finished, outcome not confirmed")
    label = {
        evolve.ATTEMPT: "Task",
        evolve.CORRECTION: "Correction",
        evolve.REJECTED_CARD: "Turned down",
    }.get(row.kind, row.kind)
    return f"{label} · {line}" + (f" · {day}" if day else "")


__all__ = [
    "FAILURE",
    "HOLDOUT",
    "SCOPE_PERSONAL",
    "SCOPE_WORKSPACE",
    "SUCCESS",
    "TRAIN",
    "UNKNOWN",
    "collect_cards",
    "collect_run",
    "family_for",
    "note_correction",
    "outcome_from",
    "record",
    "split_for",
    "summary",
]
