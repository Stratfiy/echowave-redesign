"""The Call and Appointment runtime: what a call may book, and booking it.

Handoff 6's contract for the Call and Appointment helper: "Answer eligible
calls, collect structured details, suggest available slots and book within
granted policy. Reliable interrupt/recover flow, caller verification
appropriate to task, no private disclosure based solely on caller ID.
Escalate when unsure."

How each part is kept:

* **Granted policy.** ``appointment_policies`` starts at ``booking = off``.
  ``suggest`` offers open times and never confirms; ``book`` confirms an open
  time. Nothing books that a person did not switch on.
* **Open slots** come from the hours the business keeps (the agent's own
  schedule, else the workspace's -- ``agent_hours``), minus appointments
  already booked, inside the policy's lead time and horizon. Hours nobody set
  are not "open all day" here, unlike answering calls: booking at 3am because
  nobody configured hours is a promise nobody will keep, so it is
  ``needs_setup`` instead.
* **Structured details.** A booking needs a name, a callback number and a
  reason; a missing one is returned to the helper to ask, never guessed.
* **No double booking.** One advisory lock per workspace around the overlap
  check and the insert, so two calls at once cannot take the same time.
* **Verification appropriate to task.** ``details`` lets anyone book with
  their details; ``known_caller`` lets only a caller already in the
  workspace's contacts book. Either way the helper is given no tool that
  reads an existing booking, so nothing private is ever disclosed because a
  caller ID matched.
* **Escalate when unsure.** ``escalate_to_person`` writes a line on the team's
  Decibyl thread with what the caller wanted, and tells the helper whether a
  person can be put through.

Off (``call_appointment``), the tool is not offered and the routes are 404s.
"""

from __future__ import annotations

import json

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert

from api.db import db_client
from api.db.voice_models import AppointmentModel, AppointmentPolicyModel
from api.services import features
from api.services.compliance import dnd

FLAG = "call_appointment"
BOOKING_MODES = ("off", "suggest", "book")
VERIFICATION_MODES = ("details", "known_caller")
MAX_SERVICES = 20
MAX_SLOTS = 6
SLOT_STEP = 15

SLOTS_TOOL = "appointment_slots"
BOOK_TOOL = "book_appointment"
ESCALATE_TOOL = "escalate_to_person"
TOOL_NAMES = (SLOTS_TOOL, BOOK_TOOL, ESCALATE_TOOL)
DISPLAY_NAME = "Appointments"
DESCRIPTION = "Offer open times and book within the workspace's appointment policy."


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


class PolicyInvalid(ValueError):
    pass


class PolicyConflict(Exception):
    def __init__(self, stored: dict[str, Any]):
        super().__init__("The policy changed since you opened it.")
        self.stored = stored


class BookingRefused(ValueError):
    """Why a booking was not made, in words the helper can say."""

    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


# --- policy -----------------------------------------------------------------


def _default_policy(organization_id: int) -> dict[str, Any]:
    return {
        "organization_id": organization_id,
        "booking": "off",
        "duration_minutes": 30,
        "lead_minutes": 60,
        "horizon_days": 14,
        "services": [],
        "verification": "details",
        "escalate_to": None,
        "call_workflow_id": None,
        "revision": 0,
        "updated_at": None,
    }


def _policy_dict(row: AppointmentPolicyModel | None, organization_id: int) -> dict:
    if row is None:
        return _default_policy(organization_id)
    return {
        "organization_id": row.organization_id,
        "booking": row.booking,
        "duration_minutes": row.duration_minutes,
        "lead_minutes": row.lead_minutes,
        "horizon_days": row.horizon_days,
        "services": list(row.services or []),
        "verification": row.verification,
        "escalate_to": row.escalate_to,
        "call_workflow_id": row.call_workflow_id,
        "revision": row.revision,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def get_policy(organization_id: int) -> dict[str, Any]:
    async with db_client.async_session() as session:
        row = await session.get(AppointmentPolicyModel, organization_id)
        return _policy_dict(row, organization_id)


def validate_policy(changes: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in changes.items():
        if key == "booking":
            if value not in BOOKING_MODES:
                raise PolicyInvalid("Booking is off, suggest or book.")
            out[key] = value
        elif key == "verification":
            if value not in VERIFICATION_MODES:
                raise PolicyInvalid("Verification is details or known_caller.")
            out[key] = value
        elif key == "duration_minutes":
            if not isinstance(value, int) or not 10 <= value <= 240 or value % 5:
                raise PolicyInvalid("An appointment lasts 10 to 240 minutes, in fives.")
            out[key] = value
        elif key == "lead_minutes":
            if not isinstance(value, int) or not 0 <= value <= 7 * 24 * 60:
                raise PolicyInvalid("Lead time is 0 minutes to 7 days.")
            out[key] = value
        elif key == "horizon_days":
            if not isinstance(value, int) or not 1 <= value <= 90:
                raise PolicyInvalid("Bookings may be 1 to 90 days ahead.")
            out[key] = value
        elif key == "services":
            if not isinstance(value, list) or len(value) > MAX_SERVICES:
                raise PolicyInvalid(f"Up to {MAX_SERVICES} services.")
            services = [str(s).strip()[:120] for s in value if str(s).strip()]
            out[key] = services
        elif key == "escalate_to":
            if value in (None, ""):
                out[key] = None
                continue
            normalised = dnd.normalise_number(value)
            if normalised is None:
                raise PolicyInvalid("That escalation number is not a phone number.")
            out[key] = dnd.to_dialable(normalised)
        elif key == "call_workflow_id":
            if value is not None and not isinstance(value, int):
                raise PolicyInvalid("Choose a helper by its id.")
            out[key] = value
        else:
            raise PolicyInvalid(f"{key} is not part of the policy.")
    return out


async def save_policy(
    organization_id: int, changes: dict[str, Any], *, revision: int, user_id: int
) -> dict[str, Any]:
    """Save if ``revision`` is current. The helper workflow must belong to
    this workspace (a foreign key proves it exists, not that it is yours)."""
    clean = validate_policy(changes)
    if clean.get("call_workflow_id") is not None:
        workflow = await db_client.get_workflow(
            clean["call_workflow_id"], organization_id=organization_id
        )
        if workflow is None:
            raise PolicyInvalid("That helper is not in this workspace.")
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        if revision == 0:
            base = {
                k: v
                for k, v in _default_policy(organization_id).items()
                if k not in ("organization_id", "revision", "updated_at")
            }
            inserted = (
                await session.execute(
                    insert(AppointmentPolicyModel)
                    .values(
                        organization_id=organization_id,
                        revision=1,
                        updated_by=user_id,
                        updated_at=now,
                        **{**base, **clean},
                    )
                    .on_conflict_do_nothing(index_elements=["organization_id"])
                    .returning(AppointmentPolicyModel.organization_id)
                )
            ).first()
            if inserted is None:
                await session.rollback()
                raise PolicyConflict(await get_policy(organization_id))
        else:
            hit = (
                await session.execute(
                    update(AppointmentPolicyModel)
                    .where(
                        AppointmentPolicyModel.organization_id == organization_id,
                        AppointmentPolicyModel.revision == revision,
                    )
                    .values(
                        revision=AppointmentPolicyModel.revision + 1,
                        updated_by=user_id,
                        updated_at=now,
                        **clean,
                    )
                    .returning(AppointmentPolicyModel.organization_id)
                )
            ).first()
            if hit is None:
                await session.rollback()
                raise PolicyConflict(await get_policy(organization_id))
        await session.commit()
    return await get_policy(organization_id)


# --- hours and slots ----------------------------------------------------------


async def _zone(organization_id: int) -> ZoneInfo:
    from api.services.integrations.google_calendar.client import booking_timezone

    try:
        return ZoneInfo(await booking_timezone(organization_id))
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Kolkata")


async def _run_configurations(
    organization_id: int, workflow_run_id: int | None
) -> dict[str, Any] | None:
    """The configurations of the version this call is running -- the draft on
    a test, the published one on a live call -- or None when the run names
    none. Scoped to the organisation like every read here."""
    if not workflow_run_id:
        return None
    from api.db.models import WorkflowDefinitionModel

    run = await db_client.get_workflow_run(
        workflow_run_id, organization_id=organization_id
    )
    definition_id = getattr(run, "definition_id", None)
    if not definition_id:
        return None
    async with db_client.async_session() as session:
        definition = await session.get(WorkflowDefinitionModel, definition_id)
    if definition is None:
        return None
    return dict(definition.workflow_configurations or {})


async def _schedule(
    organization_id: int,
    workflow_id: int | None,
    workflow_run_id: int | None = None,
) -> Any:
    """The hours that apply, or None when nobody set any.

    The agent's own hours come from the version the call runs on (a test
    runs the draft), else from the published version, else the
    organisation's hours. Reading only the published version refused every
    booking a test of a new receptionist tried: its hours were in the draft
    the owner had just saved."""
    from api.services.organization_preferences import get_organization_preferences
    from api.services.workflow import agent_hours

    agent_schedule = None
    running = await _run_configurations(organization_id, workflow_run_id)
    if running is not None:
        agent_schedule = running.get(agent_hours.CONFIG_KEY)
    elif workflow_id:
        workflow = await db_client.get_workflow(
            workflow_id, organization_id=organization_id
        )
        if workflow is not None:
            released = await db_client.get_released_configurations(workflow)
            agent_schedule = (released or {}).get(agent_hours.CONFIG_KEY)
    organization_hours = None
    try:
        preferences = await get_organization_preferences(organization_id)
        organization_hours = getattr(preferences, "business_hours", None)
        if hasattr(organization_hours, "model_dump"):
            organization_hours = organization_hours.model_dump()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read the organization's hours: {}", exc)
    schedule = agent_hours.effective_schedule(agent_schedule, organization_hours)
    if not isinstance(schedule, dict) or not schedule.get("enabled"):
        return None
    if not schedule.get("slots"):
        return None
    return schedule


async def _booked_spans(
    session, organization_id: int, day_start: datetime, day_end: datetime
) -> list[tuple[datetime, datetime]]:
    rows = (
        await session.execute(
            select(AppointmentModel.starts_at, AppointmentModel.ends_at).where(
                AppointmentModel.organization_id == organization_id,
                AppointmentModel.status == "booked",
                AppointmentModel.starts_at < day_end,
                AppointmentModel.ends_at > day_start,
            )
        )
    ).all()
    return [(_aware(a), _aware(b)) for a, b in rows]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def open_slots(
    *,
    organization_id: int,
    day: date,
    workflow_id: int | None = None,
    workflow_run_id: int | None = None,
    now: datetime | None = None,
    limit: int = MAX_SLOTS,
) -> dict[str, Any]:
    """Up to ``limit`` open start times on ``day`` (local), with the state."""
    from api.services.workflow import agent_hours

    policy = await get_policy(organization_id)
    if policy["booking"] == "off":
        return {
            "status": "booking_off",
            "slots": [],
            "say": (
                "Booking is not switched on here. Take their details and say the "
                "team will call back."
            ),
        }
    schedule = await _schedule(
        organization_id,
        workflow_id or policy["call_workflow_id"],
        workflow_run_id,
    )
    if schedule is None:
        return {
            "status": "needs_setup",
            "slots": [],
            "say": (
                "Opening hours are not set, so no time can be offered. Take their "
                "details and say the team will call back."
            ),
        }
    zone = await _zone(organization_id)
    now = now or datetime.now(UTC)
    earliest = now + timedelta(minutes=policy["lead_minutes"])
    latest = now + timedelta(days=policy["horizon_days"])
    day_start = datetime.combine(day, datetime.min.time(), tzinfo=zone)
    day_end = day_start + timedelta(days=1)
    if day_end <= earliest or day_start >= latest:
        return {
            "status": "outside_policy",
            "slots": [],
            "say": ("That day is outside the times bookings can be made."),
        }
    async with db_client.async_session() as session:
        booked = await _booked_spans(session, organization_id, day_start, day_end)
    busy = []
    for start, end in booked:
        first = max(start, day_start) - day_start
        last = min(end, day_end) - day_start
        busy.append((int(first.total_seconds() // 60), int(last.total_seconds() // 60)))
    duration = policy["duration_minutes"]
    slots: list[str] = []
    for window_start, window_end in agent_hours.open_windows(schedule, day):
        minute = -(-window_start // SLOT_STEP) * SLOT_STEP
        while minute + duration <= window_end and len(slots) < limit:
            start = day_start + timedelta(minutes=minute)
            finish = minute + duration
            clash = any(finish > b0 and minute < b1 for b0, b1 in busy)
            if (
                not clash
                and earliest <= start
                and start + timedelta(minutes=duration) <= latest
            ):
                slots.append(start.strftime("%H:%M"))
            minute += SLOT_STEP
    return {
        "status": "ok" if slots else "full",
        "date": day.isoformat(),
        "timezone": zone.key,
        "duration_minutes": duration,
        "slots": slots,
        "may_book": policy["booking"] == "book",
    }


# --- booking ------------------------------------------------------------------


async def book(
    *,
    organization_id: int,
    starts_at: datetime,
    caller_name: str,
    caller_number: str,
    reason: str,
    service: str | None = None,
    workflow_id: int | None = None,
    workflow_run_id: int | None = None,
    caller_known: bool = False,
    source: str = "call",
    booked_by_user_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Book one appointment within policy. Raises BookingRefused."""
    policy = await get_policy(organization_id)
    if policy["booking"] != "book":
        raise BookingRefused(
            "Booking is not allowed here; offer to have the team call back.",
            code="booking_not_allowed",
        )
    missing = [
        label
        for label, value in (
            ("name", caller_name),
            ("phone number", caller_number),
            ("reason", reason),
        )
        if not (value or "").strip()
    ]
    if missing:
        raise BookingRefused(
            f"Ask for their {', '.join(missing)} before booking.",
            code="details_missing",
        )
    number = dnd.normalise_number(caller_number)
    if number is None:
        raise BookingRefused("Ask for the phone number again.", code="details_missing")
    if policy["verification"] == "known_caller" and not caller_known:
        raise BookingRefused(
            "Only callers the team already knows can book by phone; offer a call back.",
            code="caller_not_verified",
        )
    services = policy["services"]
    if services and service and service not in services:
        raise BookingRefused(
            f"That is not one of the services: {', '.join(services)}.",
            code="unknown_service",
        )
    zone = await _zone(organization_id)
    starts_at = starts_at if starts_at.tzinfo else starts_at.replace(tzinfo=zone)
    local_day = starts_at.astimezone(zone).date()
    # Every open start that day, not only the first few a caller is offered.
    offered = await open_slots(
        organization_id=organization_id,
        day=local_day,
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        now=now,
        limit=24 * 60 // SLOT_STEP,
    )
    wanted = starts_at.astimezone(zone).strftime("%H:%M")
    if offered.get("status") not in ("ok", "full"):
        raise BookingRefused(
            offered.get("say") or "That time cannot be booked.",
            code=str(offered.get("status")),
        )
    ends_at = starts_at + timedelta(minutes=policy["duration_minutes"])
    async with db_client.async_session() as session:
        # One booking at a time per workspace: the overlap check and the
        # insert happen under the same lock, so a second call cannot slip in
        # between them.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:k1, :k2)"),
            {"k1": 20261008, "k2": organization_id},
        )
        clash = await _booked_spans(session, organization_id, starts_at, ends_at)
        if clash or wanted not in set(offered.get("slots") or []):
            await session.rollback()
            raise BookingRefused(
                "That time is no longer open; offer another.", code="slot_taken"
            )
        row = AppointmentModel(
            organization_id=organization_id,
            starts_at=starts_at,
            ends_at=ends_at,
            status="booked",
            service=(service or None),
            caller_name=caller_name.strip()[:120],
            caller_number=dnd.to_dialable(number),
            reason=reason.strip()[:500],
            source=source,
            workflow_run_id=workflow_run_id,
            booked_by_user_id=booked_by_user_id,
            created_at=datetime.now(UTC),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return appointment_dict(row)


def appointment_dict(row: AppointmentModel) -> dict[str, Any]:
    return {
        "id": row.id,
        "starts_at": _aware(row.starts_at).isoformat(),
        "ends_at": _aware(row.ends_at).isoformat(),
        "status": row.status,
        "service": row.service,
        "caller_name": row.caller_name,
        "caller_number": row.caller_number,
        "reason": row.reason,
        "source": row.source,
        "workflow_run_id": row.workflow_run_id,
    }


async def upcoming(organization_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    async with db_client.async_session() as session:
        rows = (
            await session.scalars(
                select(AppointmentModel)
                .where(
                    AppointmentModel.organization_id == organization_id,
                    AppointmentModel.status == "booked",
                    AppointmentModel.ends_at >= datetime.now(UTC),
                )
                .order_by(AppointmentModel.starts_at)
                .limit(limit)
            )
        ).all()
    return [appointment_dict(r) for r in rows]


# --- escalation ---------------------------------------------------------------


async def escalate(
    *,
    organization_id: int,
    reason: str,
    caller_number: str | None,
    workflow_run_id: int | None,
) -> dict[str, Any]:
    """Hand the caller to a person: a line on the team's thread, and whether
    a person can be put through now."""
    from api.enums import AgentEventKind
    from api.services.workflow import agent_timeline

    policy = await get_policy(organization_id)
    shown = reason.strip()[:300] or "The caller needs a person."
    try:
        await agent_timeline.record(
            organization_id=organization_id,
            kind=AgentEventKind.ACTIVITY.value,
            summary=f"A caller needs a person: {shown}",
            payload={
                "from": "Call and Appointment",
                "escalation": True,
                "caller": caller_number,
                "workflow_run_id": workflow_run_id,
            },
            in_channel=False,
        )
    except Exception as exc:  # noqa: BLE001 - the caller must still be told
        logger.warning("Could not record an escalation: {}", exc)
    if policy["escalate_to"]:
        return {
            "status": "escalated",
            "transfer_to": policy["escalate_to"],
            "say": "Tell the caller you are putting them through to a person now.",
        }
    return {
        "status": "escalated",
        "transfer_to": None,
        "say": "Tell the caller a person from the team will call them back.",
    }


# --- the built-in tool a call uses ------------------------------------------


def is_appointments_tool(tool: Any) -> bool:
    from api.enums import ToolCategory

    return getattr(tool, "category", None) == ToolCategory.APPOINTMENTS.value


def function_schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": SLOTS_TOOL,
            "description": (
                "Open appointment times on one day, inside the business's hours "
                "and booking policy. Ask before offering a time."
            ),
            "properties": {
                "date": {"type": "string", "description": "The day, YYYY-MM-DD."}
            },
            "required": ["date"],
        },
        {
            "name": BOOK_TOOL,
            "description": (
                "Book one open time for the caller. Needs their name, callback "
                "number and reason. Never read out other bookings."
            ),
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "time": {
                    "type": "string",
                    "description": "HH:MM, one of the open times",
                },
                "name": {"type": "string"},
                "phone_number": {"type": "string"},
                "reason": {"type": "string"},
                "service": {"type": "string"},
            },
            "required": ["date", "time", "name", "phone_number", "reason"],
        },
        {
            "name": ESCALATE_TOOL,
            "description": (
                "Hand the caller to a person when you are unsure, when they ask "
                "for one, or when they ask about an existing booking."
            ),
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    ]


async def run_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    organization_id: int,
    workflow_id: int | None,
    workflow_run_id: int | None,
    call_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One call to the appointments tool from a live call. Never raises:
    every refusal is a sentence for the helper to say."""
    context = call_context or {}
    if not enabled(organization_id):
        return {
            "status": "unavailable",
            "say": "Booking is not available; offer a call back.",
        }
    try:
        if name == SLOTS_TOOL:
            day = date.fromisoformat(str(arguments.get("date") or ""))
            return await open_slots(
                organization_id=organization_id,
                day=day,
                workflow_id=workflow_id,
                workflow_run_id=workflow_run_id,
            )
        if name == BOOK_TOOL:
            day = date.fromisoformat(str(arguments.get("date") or ""))
            hh, _, mm = str(arguments.get("time") or "").partition(":")
            zone = await _zone(organization_id)
            starts = datetime.combine(
                day, datetime.min.time(), tzinfo=zone
            ) + timedelta(hours=int(hh), minutes=int(mm))
            booked = await book(
                organization_id=organization_id,
                starts_at=starts,
                caller_name=str(arguments.get("name") or ""),
                caller_number=str(arguments.get("phone_number") or ""),
                reason=str(arguments.get("reason") or ""),
                service=(str(arguments.get("service") or "") or None),
                workflow_id=workflow_id,
                workflow_run_id=workflow_run_id,
                caller_known=bool(context.get("contact_is_known")),
                source="call_for_me"
                if context.get("trigger_source") == "call_for_me"
                else "call",
            )
            return {
                "status": "booked",
                "starts_at": booked["starts_at"],
                "say": "Confirm the day and time back to the caller.",
            }
        if name == ESCALATE_TOOL:
            return await escalate(
                organization_id=organization_id,
                reason=str(arguments.get("reason") or ""),
                caller_number=context.get("caller_number"),
                workflow_run_id=workflow_run_id,
            )
    except BookingRefused as exc:
        return {"status": "refused", "reason_code": exc.code, "say": str(exc)}
    except (ValueError, TypeError):
        return {
            "status": "refused",
            "reason_code": "bad_date",
            "say": "Ask for the day and time again.",
        }
    except Exception as exc:  # noqa: BLE001 - a call must hear something
        logger.error("Appointments tool {} failed: {}", name, exc)
        return {
            "status": "failed",
            "say": "Booking is not working right now; offer a call back.",
        }
    return {"status": "unavailable", "say": "No such tool."}


async def ensure_tool(*, organization_id: int, user_id: int) -> str | None:
    """The workspace's one appointments tool row, made on first ask."""
    from api.enums import ToolCategory, ToolStatus

    if not enabled(organization_id):
        return None
    existing = await db_client.get_tools_for_organization(
        organization_id,
        status=ToolStatus.ACTIVE.value,
        category=ToolCategory.APPOINTMENTS.value,
    )
    for row in existing:
        return str(row.tool_uuid)
    created = await db_client.create_tool(
        organization_id=organization_id,
        user_id=user_id,
        name=DISPLAY_NAME,
        definition={"schema_version": 1, "type": "appointments"},
        category=ToolCategory.APPOINTMENTS.value,
        description=DESCRIPTION,
        icon="calendar-check",
        icon_color="#075A39",
    )
    return str(created.tool_uuid)


async def attach_to_agent(
    *, organization_id: int, workflow_id: int, tool_uuid: str
) -> int:
    """Put the booking tool on the talking steps of the agent the policy
    names, in its draft. Returns how many steps gained it (0 when they all
    had it already).

    Choosing "the helper that answers booking calls" is the owner's choice
    that this agent books; leaving the tool for them to find in the editor
    was a dead end they could not see. A draft, like every change made for
    an owner -- a person publishes -- and appended, never replacing a tool a
    step already holds.
    """
    from api.services.workflow import brief_apps

    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        return 0
    draft = await db_client.get_draft_version(workflow.id)
    source = (
        draft.workflow_json
        if draft is not None
        else (
            workflow.released_definition.workflow_json
            if getattr(workflow, "released_definition", None) is not None
            else workflow.workflow_definition
        )
    )
    definition = json.loads(json.dumps(source or {}))
    before = sum(
        tool_uuid in ((n.get("data") or {}).get("tool_uuids") or [])
        for n in definition.get("nodes") or []
        if isinstance(n, dict)
    )
    definition = brief_apps.attach(definition, [tool_uuid])
    after = sum(
        tool_uuid in ((n.get("data") or {}).get("tool_uuids") or [])
        for n in definition.get("nodes") or []
        if isinstance(n, dict)
    )
    if after == before:
        return 0
    await db_client.update_workflow(
        workflow.id,
        name=None,
        workflow_definition=definition,
        template_context_variables=None,
        workflow_configurations=None,
        organization_id=organization_id,
    )
    return after - before
