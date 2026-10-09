"""What a model is told about the account's clock and its standing plans.

Three readings that had a screen promising them and no prompt carrying them:

* **Now.** The agents' prompts open with the date and time
  (``compose_today_line``); Decibyl's had none, so "every weekday at 9",
  "what is due tomorrow" and a ``recall`` date range were all worked out from
  whatever year the model remembers.
* **Schedules.** The Schedules screen lists every routine with its next run,
  and neither Decibyl nor the agent that runs one could say what was
  scheduled. Asked "what do you do every morning?", an agent with a routine
  on it had nothing to read.
* **The person's own reminders.** Today's reminders and events, and Care's
  medicine reminders, are the asker's own and were invisible to the
  assistant that set them -- so "what are my reminders?" had no answer and
  "remind me to take my BP tablet" could propose the same reminder twice.

Every reading here never raises: each is one reading of several, and a
reading that fails is a section that says less, logged, never a turn that
fails. Nothing here filters out a row it does not understand -- a routine
whose schedule cannot be read is listed as such rather than dropped
(api/AGENTS.md, Silent Absence).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from loguru import logger

from api.db import db_client
from api.services import prompt_budget

#: Routines named in one prompt. The Schedules screen of a busy account
#: runs to a dozen; past this the model is told how many were left out.
MAX_ROUTINES = 20
#: How much of a routine's instruction is carried. Enough to say what the
#: run does; the run itself is handed the whole instruction.
MAX_INSTRUCTION_CHARS = 240
#: The person's own reminders, events and medicine reminders, together.
MAX_PERSONAL = 15


async def zone_for(organization_id: int | None, user_id: int | None) -> str:
    """The person's own zone, else the workspace's, else the default.

    The same order Today uses, so "tomorrow" means the same day in the chat
    as on the screen beside it. Never raises."""
    from api.services.today.scope import zone_for as today_zone

    try:
        return await today_zone(user_id, organization_id)
    except Exception as exc:  # noqa: BLE001 - one reading of several
        logger.warning("Could not read the timezone for the turn: {}", exc)
        from api.services.today.scope import DEFAULT_TIMEZONE

        return DEFAULT_TIMEZONE


def now_line(zone: str | None) -> str:
    """What day and time it is, in the same words an agent's prompt opens
    with (``compose_today_line``), so the two never disagree."""
    from api.services.workflow.pipecat_engine_context_composer import (
        compose_today_line,
    )

    return compose_today_line(zone)


# --- schedules ---------------------------------------------------------------


def _routine_line(
    row: Any,
    *,
    zone: ZoneInfo,
    business_hours: Any,
    now: datetime,
    owner: str | None,
) -> str:
    from api.services.today.scope import full_local
    from api.services.workflow import routines

    name = str(getattr(row, "name", "") or "a routine")
    who = f" ({owner})" if owner else ""
    try:
        spec = routines.spec_from_model(row)
        schedule = routines.describe(spec)
        if spec.is_active:
            nxt = routines.next_slot(
                spec, now=now, zone=zone, business_hours=business_hours
            )
            state = (
                f"on, next run {full_local(nxt, zone.key)}"
                if nxt
                else "on, but it does not come round in the next two weeks"
            )
        else:
            state = "off (saved, not switched on)"
    except Exception as exc:  # noqa: BLE001 - listed, never dropped
        logger.warning("Routine {} has a schedule that cannot be read: {}", row, exc)
        schedule = "a schedule that cannot be read"
        state = "check it on the Schedules screen"
    task = prompt_budget.clip(
        " ".join(str(getattr(row, "instruction", "") or "").split()),
        MAX_INSTRUCTION_CHARS,
    )
    line = f"- {name}{who}: {schedule}; {state}."
    if task:
        line += f" Task: {task}"
    skipped = getattr(row, "last_skipped_reason", None)
    if skipped:
        line += f" Last skipped: {skipped}."
    return line


async def _visible_to(
    row: Any, viewer_id: Optional[int], thread_id: Optional[str]
) -> bool:
    """Whether a routine may be named on this turn.

    An agent's routine is the workspace's. Decibyl's own may have been set
    in somebody's private chat, and its name and instruction are their
    words: shown to them, and on a turn with nobody signed in only on the
    conversation it was set in (where its own runs report)."""
    if getattr(row, "workflow_id", None) is not None:
        return True
    from api.services.workflow import routines

    if viewer_id is not None:
        return not await routines.hidden_from(row, viewer_id)
    card = await routines.origin_card(
        row.organization_id, getattr(row, "armed_by_card_event_id", None)
    )
    return card is None or card.thread_id == thread_id


def routines_block(
    rows: list[Any],
    *,
    zone_name: str,
    business_hours: Any = None,
    names: dict[int, str] | None = None,
    now: datetime | None = None,
) -> str:
    """The routines as lines: name, whose, schedule, on/off and next run,
    and what each run is asked to do. "" for none."""
    if not rows:
        return ""
    try:
        zone = ZoneInfo(zone_name)
    except Exception:  # noqa: BLE001 - an unknown zone must not lose the list
        from api.services.today.scope import DEFAULT_TIMEZONE

        zone = ZoneInfo(DEFAULT_TIMEZONE)
    moment = now or datetime.now(UTC)
    lines = []
    for row in rows:
        owner = None
        if names is not None:
            workflow_id = getattr(row, "workflow_id", None)
            owner = (
                "yours, Decibyl's"
                if workflow_id is None
                else names.get(workflow_id) or "an agent"
            )
        lines.append(
            _routine_line(
                row,
                zone=zone,
                business_hours=business_hours,
                now=moment,
                owner=owner,
            )
        )
    return "\n".join(prompt_budget.lines(lines, max_items=MAX_ROUTINES, noun="routine"))


async def _hours_and_zone(organization_id: int, zone_name: str | None) -> tuple:
    from api.services.organization_preferences import get_organization_preferences

    try:
        preferences = await get_organization_preferences(organization_id)
    except Exception as exc:  # noqa: BLE001 - one reading of several
        logger.warning("Could not read business hours for routines: {}", exc)
        preferences = None
    if zone_name is None:
        from api.services.compliance import dnd

        zone_name = dnd.resolve_zone(getattr(preferences, "timezone", None)).key
    return getattr(preferences, "business_hours", None), zone_name


async def account_routines(
    organization_id: int,
    *,
    viewer_id: Optional[int],
    thread_id: Optional[str],
    names: dict[int, str],
) -> str:
    """Every routine in the account that this turn may see, for Decibyl.

    The routine itself runs on the workspace's clock (routes/routines), so
    its next run is said in the workspace's zone, not the asker's."""
    try:
        rows = list(
            await db_client.routines_for_organization(organization_id=organization_id)
        )
        visible = [r for r in rows if await _visible_to(r, viewer_id, thread_id)]
        if not visible:
            return ""
        hours, zone_name = await _hours_and_zone(organization_id, None)
        return routines_block(
            visible, zone_name=zone_name, business_hours=hours, names=names
        )
    except Exception as exc:  # noqa: BLE001 - one reading of several
        logger.warning("Could not read the routines: {}", exc)
        return ""


async def agent_routines(organization_id: int | None, workflow_id: int | None) -> str:
    """One agent's own routines, for its prompt. "" for none."""
    if not organization_id or not workflow_id:
        return ""
    try:
        rows = list(
            await db_client.routines_for_workflow(
                workflow_id, organization_id=organization_id
            )
        )
        if not rows:
            return ""
        hours, zone_name = await _hours_and_zone(organization_id, None)
        body = routines_block(rows, zone_name=zone_name, business_hours=hours)
    except Exception as exc:  # noqa: BLE001 - the call must go on
        logger.warning("Could not read this agent's routines: {}", exc)
        return ""
    return (
        "YOUR SCHEDULE.\n"
        "These are the routines set on you. A routine runs on its own at the "
        "time shown and is handed its task; answer questions about what you "
        "do and when from this list, and do not promise a run that is not on "
        "it.\n" + body
    )


# --- the person's own --------------------------------------------------------


async def personal_block(organization_id: int, user_id: Optional[int]) -> str:
    """The asker's own reminders, events and medicine reminders. "" for
    nobody signed in, nothing set, or the features off."""
    if not user_id:
        return ""
    from api.services import features

    lines: list[str] = []
    try:
        from api.services import today
        from api.services.today import reminders as today_reminders
        from api.services.today.scope import Viewer

        if features.is_on(today.REMINDERS, organization_id):
            zone_name = await zone_for(organization_id, user_id)
            listed = await today_reminders.list_for(
                Viewer(
                    user_id=user_id,
                    organization_id=organization_id,
                    zone_name=zone_name,
                )
            )
            for event in listed.get("events") or []:
                lines.append(f"- Event: {event['title']}, {event['when']}")
            for reminder in listed.get("reminders") or []:
                when = reminder.get("when") or reminder.get("offset_words") or ""
                repeat = reminder.get("recurrence")
                repeat = f", {repeat}" if repeat and repeat != "once" else ""
                lines.append(
                    f"- Reminder: {reminder['title']}, {when}{repeat} "
                    f"({reminder.get('status')})"
                )
    except Exception as exc:  # noqa: BLE001 - one reading of several
        logger.warning("Could not read the person's reminders: {}", exc)
    try:
        from api.services.care import medicines

        if medicines.enabled(organization_id):
            for med in await medicines.list_mine(organization_id, user_id):
                times = ", ".join(med.get("times") or [])
                lines.append(
                    f"- Medicine reminder: {med['label']} at {times} "
                    f"({med.get('state')}, by {med.get('channel')})"
                )
    except Exception as exc:  # noqa: BLE001 - one reading of several
        logger.warning("Could not read the person's medicine reminders: {}", exc)
    if not lines:
        return ""
    return "\n".join(
        prompt_budget.lines(
            [prompt_budget.clip(line, 300) for line in lines],
            max_items=MAX_PERSONAL,
            noun="reminder",
        )
    )


__all__ = [
    "account_routines",
    "agent_routines",
    "now_line",
    "personal_block",
    "routines_block",
    "zone_for",
]
