"""HTTP surface for a bot's routines.

The ignition for machinery that shipped without one: ``agent_routines``, the
minute tick and the runner all existed with no route touching them, so a
routine could only be created by writing SQL -- which meant the one pack that
needs a routine could not be given one.

Everything here is org-scoped from the authenticated user, never from the
request body. A routine id in a URL proves a row exists; it does not prove the
caller may touch it.

Two rules the routes enforce that the runtime enforces again:

**Arming is its own endpoint, and it refuses without a test run.** Folding
``is_active`` into a general save would make the precondition a surprise in
the middle of an edit, and the first time a bot runs unsupervised it writes
into somebody's real accounting software.

**Deleting a bot takes its routines with it.** That is the FK's ``CASCADE``
rather than anything here, but it is why nothing in this file has to sweep for
orphans.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from api.db import db_client
from api.db.models import AgentRoutineModel, UserModel
from api.enums import OrganizationRole
from api.schemas.routine import (
    RoutineListResponse,
    RoutineResponse,
    RoutineTestResponse,
    RoutineWrite,
)
from api.services.auth.depends import get_user, require_organization_role
from api.services.compliance import dnd
from api.services.organization_preferences import get_organization_preferences
from api.services.workflow import routines as routine_rules
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames

router = APIRouter(prefix="/workflows/{workflow_id}/routines", tags=["routines"])

#: How many a single bot may have. A bot with thirty routines is not a bot
#: anybody can reason about, and the tick reads every armed one every minute.
MAX_PER_WORKFLOW = 10


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


async def _owned_workflow(workflow_id: int, organization_id: int):
    """The bot, or 404. Org-scoped: the id came from a URL."""
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="No such agent in this account.")
    return workflow


async def _render(
    routine: AgentRoutineModel, *, organization_id: int
) -> RoutineResponse:
    """One routine with its derived answers filled in.

    The derived fields are computed here rather than by the screen, because
    every one of them -- when it runs next, may it arm, how the schedule reads
    -- is a function of the schedule and the business's hours, and a screen
    that recomputed them would eventually disagree with the tick that actually
    fires.
    """
    spec = routine_rules.spec_from_model(routine)
    preferences = await get_organization_preferences(organization_id)
    zone = dnd.resolve_zone(getattr(preferences, "timezone", None))

    return RoutineResponse(
        id=routine.id,
        workflow_id=routine.workflow_id,
        name=routine.name,
        instruction=routine.instruction or "",
        cadence=routine.cadence,
        anchor=routine.anchor,
        at_minute=int(routine.at_minute or 0),
        offset_minutes=int(routine.offset_minutes or 0),
        weekday=int(routine.weekday or 0),
        needs_apps=list(routine.needs_apps or []),
        is_active=bool(routine.is_active),
        tested_at=routine.tested_at,
        may_arm=routine_rules.may_arm(spec),
        last_fired_at=routine.last_fired_at,
        last_skipped_reason=routine.last_skipped_reason,
        last_skipped_at=routine.last_skipped_at,
        next_run_at=routine_rules.next_slot(
            spec,
            now=datetime.now(zone),
            zone=zone,
            business_hours=getattr(preferences, "business_hours", None),
        ),
        schedule_summary=routine_rules.describe(spec),
    )


@router.get("", response_model=RoutineListResponse)
async def list_routines(
    workflow_id: int,
    user: Annotated[UserModel, Depends(get_user)],
) -> RoutineListResponse:
    """This bot's routines, armed or not."""
    organization_id = _organization_id(user)
    await _owned_workflow(workflow_id, organization_id)
    rows = await db_client.routines_for_workflow(
        workflow_id, organization_id=organization_id
    )
    return RoutineListResponse(
        routines=[await _render(r, organization_id=organization_id) for r in rows]
    )


@router.post("", response_model=RoutineResponse, status_code=201)
async def create_routine(
    workflow_id: int,
    body: RoutineWrite,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
) -> RoutineResponse:
    """Add a routine. Off until somebody arms it, which needs a test run first.

    ADMIN, matching connectors: a routine acts unsupervised against connected
    apps, so creating one is closer to granting access than to editing a
    prompt.
    """
    organization_id = _organization_id(user)
    await _owned_workflow(workflow_id, organization_id)

    existing = await db_client.routines_for_workflow(
        workflow_id, organization_id=organization_id
    )
    if len(existing) >= MAX_PER_WORKFLOW:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This agent already has {MAX_PER_WORKFLOW} routines. Edit or "
                "remove one rather than adding another."
            ),
        )

    routine = await db_client.create_routine(
        organization_id=organization_id,
        workflow_id=workflow_id,
        **body.model_dump(mode="json"),
    )
    return await _render(routine, organization_id=organization_id)


@router.put("/{routine_id}", response_model=RoutineResponse)
async def update_routine(
    workflow_id: int,
    routine_id: int,
    body: RoutineWrite,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
) -> RoutineResponse:
    """Change a routine's schedule or instruction.

    A change to the schedule clears the stored skip reason -- it described the
    old schedule, and leaving it would have the screen explain a decision that
    no longer applies. It does NOT clear ``tested_at``: the test proved the
    bot can do the job against real connectors, and moving the run to nine
    o'clock does not unprove that.
    """
    organization_id = _organization_id(user)
    await _owned_workflow(workflow_id, organization_id)

    routine = await db_client.update_routine(
        routine_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
        **body.model_dump(mode="json"),
    )
    if routine is None:
        raise HTTPException(status_code=404, detail="No such routine on this agent.")
    return await _render(routine, organization_id=organization_id)


@router.delete("/{routine_id}", status_code=204)
async def delete_routine(
    workflow_id: int,
    routine_id: int,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
) -> None:
    organization_id = _organization_id(user)
    await _owned_workflow(workflow_id, organization_id)
    removed = await db_client.delete_routine(
        routine_id, organization_id=organization_id, workflow_id=workflow_id
    )
    if not removed:
        raise HTTPException(status_code=404, detail="No such routine on this agent.")


@router.post("/{routine_id}/active", response_model=RoutineResponse)
async def set_active(
    workflow_id: int,
    routine_id: int,
    active: bool,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
) -> RoutineResponse:
    """Switch a routine on or off.

    **On requires a test run.** Refused with the reason rather than silently
    ignored, because a toggle that appears to move and does nothing is worse
    than one that will not move: the operator walks away believing the bot is
    watching their deadlines.

    Switching off never refuses. Whatever state a routine is in, stopping it
    must always be possible.
    """
    organization_id = _organization_id(user)
    await _owned_workflow(workflow_id, organization_id)

    routine = await db_client.get_routine(routine_id, organization_id=organization_id)
    if routine is None or routine.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="No such routine on this agent.")

    if active and not routine_rules.may_arm(routine_rules.spec_from_model(routine)):
        raise HTTPException(
            status_code=400,
            detail=(
                "Test run it first. The first time this runs unsupervised it "
                "acts on real data, so the test is the one chance to see what "
                "it would do."
            ),
        )

    updated = await db_client.set_routine_active(
        routine_id, organization_id=organization_id, active=active
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="No such routine on this agent.")
    return await _render(updated, organization_id=organization_id)


@router.post("/{routine_id}/test", response_model=RoutineTestResponse)
async def test_routine(
    workflow_id: int,
    routine_id: int,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
) -> RoutineTestResponse:
    """Run it once, now, on purpose.

    The real run: the published bot, its real connectors, its real data. A
    dry run that only described what it would do would prove nothing about
    whether the connectors work, which is the thing that actually breaks.

    ``tested_at`` is stamped when the run is *enqueued*, not when it finishes.
    The alternative is a toggle that stays refused because a worker was slow,
    with nothing on the screen explaining why -- and the operator watching the
    run happen in the thread is the evidence the gate is really asking for.
    """
    organization_id = _organization_id(user)
    await _owned_workflow(workflow_id, organization_id)

    routine = await db_client.get_routine(routine_id, organization_id=organization_id)
    if routine is None or routine.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="No such routine on this agent.")

    stamped = await db_client.mark_routine_tested(
        routine_id, organization_id=organization_id
    )
    if not stamped:
        raise HTTPException(status_code=404, detail="No such routine on this agent.")

    try:
        await enqueue_job(FunctionNames.RUN_AGENT_ROUTINE, routine_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not enqueue test run for routine {}", routine_id)
        raise HTTPException(
            status_code=503,
            detail="Could not start the test run just now. Try again in a moment.",
        ) from exc

    return RoutineTestResponse(
        started=True,
        workflow_id=workflow_id,
        detail=(
            "Running now. Watch the thread -- it will report what it found, or "
            "say plainly that it could not."
        ),
    )
