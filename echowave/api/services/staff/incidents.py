"""Incidents and their approved runbooks (screen 41).

An incident records impact, owner and state, and a numbered list of steps:
preflight, approval, execution, verification and notes. Execution itself is
never done here -- the allowlisted operations (pause a capability, drain or
restart workers) are the ops stream's typed commands, and a step links to
the ops command id that carried it out. There is no terminal.

States: investigating, mitigating, waiting_approval, running,
verification_failed, resolved. **Resolving needs a passed verification**
after the last execution step: an accepted restart is not a resolved
incident, and a failed verification keeps it open. Every change names the
revision it read, so two people editing at once cannot overwrite each other.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.db.staff_models import StaffIncidentModel, StaffIncidentStepModel
from api.services.staff import commands

STATES = (
    "investigating",
    "mitigating",
    "waiting_approval",
    "running",
    "verification_failed",
    "resolved",
)
OPEN_STATES = STATES[:-1]


def _view(
    i: StaffIncidentModel, steps: list[StaffIncidentStepModel] | None = None
) -> dict[str, Any]:
    out = {
        "id": i.id,
        "title": i.title,
        "impact": i.impact,
        "severity": i.severity,
        "state": i.state,
        "environment": i.environment,
        "owner_user_id": i.owner_user_id,
        "links": i.links or {},
        "opened_by": i.opened_by,
        "revision": i.revision,
        "opened_at": i.opened_at.isoformat(),
        "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
    }
    if steps is not None:
        out["steps"] = [
            {
                "sequence": s.sequence,
                "kind": s.kind,
                "summary": s.summary,
                "outcome": s.outcome,
                "ops_command_id": s.ops_command_id,
                "actor_user_id": s.actor_user_id,
                "at": s.created_at.isoformat(),
            }
            for s in steps
        ]
    return out


async def list_incidents(
    session: AsyncSession, *, open_only: bool = False, limit: int = 50
) -> list[dict]:
    q = (
        select(StaffIncidentModel)
        .order_by(StaffIncidentModel.opened_at.desc())
        .limit(limit)
    )
    if open_only:
        q = q.where(StaffIncidentModel.state.in_(OPEN_STATES))
    return [_view(i) for i in (await session.execute(q)).scalars().all()]


async def get(session: AsyncSession, incident_id: int) -> dict | None:
    i = await session.get(StaffIncidentModel, incident_id)
    if i is None:
        return None
    steps = (
        (
            await session.execute(
                select(StaffIncidentStepModel)
                .where(StaffIncidentStepModel.incident_id == incident_id)
                .order_by(StaffIncidentStepModel.sequence)
            )
        )
        .scalars()
        .all()
    )
    return _view(i, list(steps))


class Links(commands.Target):
    release: str | None = Field(default=None, max_length=64)
    providers: list[str] = Field(default_factory=list, max_length=10)
    support_cases: list[int] = Field(default_factory=list, max_length=20)
    ops_commands: list[int] = Field(default_factory=list, max_length=20)


class OpenTarget(commands.Target):
    title: str = Field(min_length=4, max_length=200)
    impact: str = Field(min_length=4, max_length=500)
    severity: Literal["sev1", "sev2", "sev3"]
    owner_user_id: int | None = Field(default=None, gt=0)
    links: Links = Field(default_factory=Links)


async def _open(
    session: AsyncSession, t: OpenTarget, actor: commands.Actor
) -> commands.Outcome:
    i = StaffIncidentModel(
        title=t.title,
        impact=t.impact,
        severity=t.severity,
        state="investigating",
        environment=constants.ENVIRONMENT,
        owner_user_id=t.owner_user_id or actor.requested_by,
        links=t.links.model_dump(),
        opened_by=actor.requested_by,
        opened_at=datetime.now(UTC),
    )
    session.add(i)
    await session.flush()
    return commands.Outcome({"incident_id": i.id})


class StepTarget(commands.Target):
    incident_id: int = Field(gt=0)
    revision: int = Field(ge=0)
    kind: Literal["note", "preflight", "approval", "execution", "verification"]
    summary: str = Field(min_length=2, max_length=500)
    outcome: Literal["passed", "failed", "pending", "unknown"] | None = None
    ops_command_id: int | None = Field(default=None, gt=0)


async def _bump(
    session: AsyncSession, incident_id: int, revision: int
) -> StaffIncidentModel:
    i = await session.get(StaffIncidentModel, incident_id, with_for_update=True)
    if i is None:
        raise commands.CommandError("There is no such incident.")
    if i.revision != revision:
        raise commands.CommandError(
            f"This incident changed since you looked at it (now revision {i.revision}). Reload it."
        )
    if i.state == "resolved":
        raise commands.CommandError("This incident is resolved; open a new one.")
    i.revision += 1
    return i


async def _step(
    session: AsyncSession, t: StepTarget, actor: commands.Actor
) -> commands.Outcome:
    if t.kind == "execution" and t.ops_command_id is None:
        raise commands.CommandError(
            "An execution step names the approved ops command that carried it out."
        )
    if t.kind in ("verification", "preflight") and t.outcome is None:
        raise commands.CommandError(
            "A check needs an outcome: passed, failed, pending or unknown."
        )
    i = await _bump(session, t.incident_id, t.revision)
    seq = (
        await session.scalar(
            select(func.coalesce(func.max(StaffIncidentStepModel.sequence), 0)).where(
                StaffIncidentStepModel.incident_id == i.id
            )
        )
    ) + 1
    session.add(
        StaffIncidentStepModel(
            incident_id=i.id,
            sequence=seq,
            kind=t.kind,
            summary=t.summary,
            outcome=t.outcome,
            ops_command_id=t.ops_command_id,
            actor_user_id=actor.requested_by,
            created_at=datetime.now(UTC),
        )
    )
    if t.kind == "verification" and t.outcome == "failed":
        i.state = "verification_failed"
    elif t.kind == "execution":
        i.state = "running"
        if t.ops_command_id not in (i.links or {}).get("ops_commands", []):
            links = dict(i.links or {})
            links["ops_commands"] = [*links.get("ops_commands", []), t.ops_command_id]
            i.links = links
    return commands.Outcome(
        {"incident_id": i.id, "sequence": seq, "revision": i.revision, "state": i.state}
    )


class StateTarget(commands.Target):
    incident_id: int = Field(gt=0)
    revision: int = Field(ge=0)
    state: Literal[
        "investigating", "mitigating", "waiting_approval", "running", "resolved"
    ]


async def _state(
    session: AsyncSession, t: StateTarget, actor: commands.Actor
) -> commands.Outcome:
    i = await _bump(session, t.incident_id, t.revision)
    if t.state == "resolved":
        steps = (
            (
                await session.execute(
                    select(StaffIncidentStepModel)
                    .where(StaffIncidentStepModel.incident_id == i.id)
                    .order_by(StaffIncidentStepModel.sequence)
                )
            )
            .scalars()
            .all()
        )
        last_exec = max((s.sequence for s in steps if s.kind == "execution"), default=0)
        verified = [
            s for s in steps if s.kind == "verification" and s.sequence > last_exec
        ]
        if not verified or verified[-1].outcome != "passed":
            raise commands.CommandError(
                "Resolve after a passed verification that follows the last execution step."
            )
        i.resolved_at = datetime.now(UTC)
    i.state = t.state
    return commands.Outcome(
        {"incident_id": i.id, "revision": i.revision, "state": i.state}
    )


for _spec in (
    commands.CommandSpec(
        name="incident.open",
        summary="Open an incident with its impact and owner.",
        request_capability="incidents.manage",
        target=OpenTarget,
        handler=_open,
        feature="staff_incidents",
    ),
    commands.CommandSpec(
        name="incident.step",
        summary="Add a runbook step: preflight, approval, execution, verification or note.",
        request_capability="incidents.manage",
        target=StepTarget,
        handler=_step,
        feature="staff_incidents",
    ),
    commands.CommandSpec(
        name="incident.state",
        summary="Move an incident; resolving needs a passed verification.",
        request_capability="incidents.manage",
        target=StateTarget,
        handler=_state,
        feature="staff_incidents",
    ),
):
    commands.register(_spec)
