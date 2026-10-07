"""Medicine reminders: what to remind, when, on which phone, in which language.

**Reminders only.** ``label`` is the person's or family's own words for the
medicine; Decibyl reads it back as written and never suggests, changes or
explains a dose. The card says so, and so does every call.

Setting reminders up rings a phone on a schedule, so it is a card
(services/care/cards.py) showing the exact number, times, language and who
is told when a dose is missed; the reminder becomes active only when the
person confirms that version. Pausing is immediate (stopping calls must
never wait); resuming is a new card.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from api.db import db_client
from api.db.care_models import CareDoseCallModel, CareMedicineModel
from api.services import member_preferences
from api.services.care import MEDICINE_CALLS, CareError, NotFound, on

AWAITING = "awaiting_approval"
ACTIVE = "active"
PAUSED = "paused"
#: The person said no to the card; nothing ever rang.
DECLINED = "declined"

MAX_TIMES = 6
MAX_MEDICINES = 12
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

#: Language names as the card and the call prompt say them.
LANGUAGE_NAMES = {
    "en-IN": "English",
    "en": "English",
    "hi-IN": "Hindi",
    "bn-IN": "Bengali",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "gu-IN": "Gujarati",
    "pa-IN": "Punjabi",
    "od-IN": "Odia",
}

#: Words that turn a reminder into dosing advice if Decibyl wrote them. A
#: label may carry the person's own instruction ("1 tablet after food"); what
#: is refused is asking Decibyl to decide one.
_ADVICE_ASKS = re.compile(
    r"\b(how much|how many|should i|what dose|which dose|double|skip if|increase|decrease)\b",
    re.IGNORECASE,
)


def enabled(organization_id: int | None = None) -> bool:
    return on(MEDICINE_CALLS, organization_id)


def _now() -> datetime:
    return datetime.now(UTC)


def normalise_phone(raw: str) -> str:
    """E.164. A bare ten-digit Indian mobile gets +91."""
    digits = re.sub(r"[^\d+]", "", raw or "")
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if not digits.startswith("+"):
        bare = digits.lstrip("0")
        if len(bare) == 10 and bare[0] in "6789":
            digits = "+91" + bare
        else:
            digits = "+" + bare
    if not re.fullmatch(r"\+\d{8,15}", digits):
        raise CareError("That does not look like a phone number.")
    return digits


def mask_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    return f"the number ending {digits[-4:]}" if len(digits) >= 4 else "the number"


def clean_times(times: Any) -> list[str]:
    if not isinstance(times, (list, tuple)) or not times:
        raise CareError("Choose at least one time for the call.")
    out: list[str] = []
    for t in times:
        t = str(t or "").strip()
        if not _TIME.match(t):
            raise CareError("A time looks like 08:00.")
        if t not in out:
            out.append(t)
    if len(out) > MAX_TIMES:
        raise CareError("At most six calls a day for one medicine.")
    return sorted(out)


def clean_label(label: str) -> str:
    label = " ".join((label or "").split())
    if not label:
        raise CareError("Say which medicine, the way you call it.")
    if len(label) > 80:
        raise CareError("Keep the medicine's name short (80 letters).")
    if _ADVICE_ASKS.search(label):
        raise CareError(
            "Decibyl only reminds; it cannot decide how much to take. Write "
            "the medicine the way the doctor told you."
        )
    return label


def say_times(times: list[str]) -> str:
    if len(times) == 1:
        return times[0]
    return ", ".join(times[:-1]) + " and " + times[-1]


async def _defaults(organization_id: int, user_id: int) -> tuple[str, str]:
    """The person's own language and timezone, else the workspace's
    timezone and English."""
    from api.services.organization_preferences import get_organization_preferences

    prefs = await member_preferences.get(user_id)
    language = prefs.get("language") or "en-IN"
    tz = prefs.get("timezone")
    if not tz:
        org = await get_organization_preferences(organization_id, db=db_client)
        tz = getattr(org, "timezone", None) or "Asia/Kolkata"
    return language, tz


def medicine_dict(row: Any, doses: list[Any] | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "label": row.label,
        "times": list(row.times or []),
        "timezone": row.timezone,
        "language": row.language,
        "language_name": LANGUAGE_NAMES.get(row.language, row.language),
        "phone_masked": mask_phone(row.phone),
        "alert_member_ids": list(row.alert_member_ids or []),
        "state": row.state,
        "card_event_id": row.card_event_id,
        "doses": [dose_dict(d) for d in (doses or [])],
    }


def dose_dict(d: Any) -> dict[str, Any]:
    return {
        "id": d.id,
        "due_at": d.due_at.isoformat(),
        "state": d.state,
        "reason": d.reason,
        "alerted": d.alerted_at is not None,
    }


async def _own(session, organization_id: int, user_id: int, medicine_id: int) -> Any:
    row = (
        await session.execute(
            select(CareMedicineModel).where(
                CareMedicineModel.id == medicine_id,
                CareMedicineModel.organization_id == organization_id,
                CareMedicineModel.person_user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("That medicine reminder is not here.")
    return row


async def list_mine(
    organization_id: int, user_id: int, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    now = now or _now()
    async with db_client.async_session() as session:
        meds = (
            await session.scalars(
                select(CareMedicineModel)
                .where(
                    CareMedicineModel.organization_id == organization_id,
                    CareMedicineModel.person_user_id == user_id,
                )
                .order_by(CareMedicineModel.id)
            )
        ).all()
        doses = (
            await session.scalars(
                select(CareDoseCallModel)
                .where(
                    CareDoseCallModel.organization_id == organization_id,
                    CareDoseCallModel.medicine_id.in_([m.id for m in meds] or [0]),
                    CareDoseCallModel.due_at >= now - timedelta(hours=24),
                )
                .order_by(CareDoseCallModel.due_at)
            )
        ).all()
    return [medicine_dict(m, [d for d in doses if d.medicine_id == m.id]) for m in meds]


async def propose(
    organization_id: int,
    user_id: int,
    *,
    label: str,
    times: list[str],
    phone: str,
    language: str | None = None,
    alert_member_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Save the reminder, waiting, and put the card in front of the person.

    Refused with NeedsSetup when reminder calls cannot be placed here: a
    reminder that silently never rings is worse than none."""
    from api.services.care import calls, circle

    label = clean_label(label)
    times = clean_times(times)
    phone = normalise_phone(phone)
    default_language, tz = await _defaults(organization_id, user_id)
    language = (language or default_language).strip()
    if language not in LANGUAGE_NAMES:
        raise CareError("That language is not offered for calls yet.")
    member_ids = [int(m) for m in (alert_member_ids or [])]
    if member_ids and not circle.enabled(organization_id):
        raise CareError("The family circle is not switched on here.")
    await circle.alertable_members(organization_id, user_id, member_ids)
    readiness = await calls.readiness(organization_id)
    if readiness["state"] == "needs_setup":
        from api.services.care import NeedsSetup

        raise NeedsSetup(readiness["reason"])
    async with db_client.async_session() as session:
        count = len(
            (
                await session.scalars(
                    select(CareMedicineModel.id).where(
                        CareMedicineModel.organization_id == organization_id,
                        CareMedicineModel.person_user_id == user_id,
                        CareMedicineModel.state.in_((ACTIVE, AWAITING, PAUSED)),
                    )
                )
            ).all()
        )
        if count >= MAX_MEDICINES:
            raise CareError("That is the most reminders one person can have.")
        row = CareMedicineModel(
            organization_id=organization_id,
            person_user_id=user_id,
            label=label,
            times=times,
            timezone=tz,
            language=language,
            phone=phone,
            alert_member_ids=member_ids,
            state=AWAITING,
            created_by_user_id=user_id,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        medicine_id = row.id
    try:
        event_id = await _propose_card(organization_id, user_id, medicine_id)
    except CareError:
        # No card, nothing to confirm: never a reminder left waiting on one.
        await not_started(
            organization_id, {"medicine_id": medicine_id, "person_user_id": user_id}
        )
        raise
    async with db_client.async_session() as session:
        row = await _own(session, organization_id, user_id, medicine_id)
        row.card_event_id = event_id
        await session.commit()
        await session.refresh(row)
        return {"medicine": medicine_dict(row), "event_id": event_id}


async def _propose_card(
    organization_id: int, user_id: int, medicine_id: int
) -> int | None:
    from api.services.care.circle import _propose
    from api.services.workflow import actions

    result = await _propose(
        organization_id,
        user_id,
        {
            "action": actions.CARE_MEDICINE_CALLS,
            "medicine_id": medicine_id,
            "person_user_id": user_id,
        },
    )
    return result.get("event_id")


async def pause(organization_id: int, user_id: int, medicine_id: int) -> dict[str, Any]:
    async with db_client.async_session() as session:
        row = await _own(session, organization_id, user_id, medicine_id)
        if row.state == ACTIVE:
            row.state = PAUSED
            row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
        return medicine_dict(row)


async def resume(
    organization_id: int, user_id: int, medicine_id: int
) -> dict[str, Any]:
    """A paused reminder rings again only after a new card is confirmed."""
    from api.services.care import NeedsSetup, calls

    readiness = await calls.readiness(organization_id)
    if readiness["state"] == "needs_setup":
        raise NeedsSetup(readiness["reason"])
    async with db_client.async_session() as session:
        row = await _own(session, organization_id, user_id, medicine_id)
        if row.state != PAUSED:
            raise CareError("Only a paused reminder can be started again.")
    event_id = await _propose_card(organization_id, user_id, medicine_id)
    async with db_client.async_session() as session:
        row = await _own(session, organization_id, user_id, medicine_id)
        row.card_event_id = event_id
        await session.commit()
        await session.refresh(row)
        return {"medicine": medicine_dict(row), "event_id": event_id}


async def mark_taken(
    organization_id: int,
    user_id: int,
    medicine_id: int,
    *,
    due_at: datetime,
) -> dict[str, Any]:
    """The person says "I took it" in the app, for a dose due today. A dose
    marked before its call means the call is not placed."""
    from sqlalchemy.dialects.postgresql import insert

    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=UTC)
    async with db_client.async_session() as session:
        row = await _own(session, organization_id, user_id, medicine_id)
        if not any(
            due_at == d
            for d in due_times(row, due_at.astimezone(ZoneInfo(row.timezone)).date())
        ):
            raise CareError("That is not one of this medicine's times.")
        await session.execute(
            insert(CareDoseCallModel)
            .values(
                organization_id=organization_id,
                medicine_id=row.id,
                due_at=due_at,
                state="taken",
                reason="marked_in_app",
                marked_by_user_id=user_id,
                outcome_at=_now(),
                created_at=_now(),
            )
            .on_conflict_do_update(
                constraint="uq_care_dose_due",
                set_={
                    "state": "taken",
                    "reason": "marked_in_app",
                    "marked_by_user_id": user_id,
                    "outcome_at": _now(),
                },
            )
        )
        await session.commit()
        dose = (
            await session.execute(
                select(CareDoseCallModel).where(
                    CareDoseCallModel.medicine_id == medicine_id,
                    CareDoseCallModel.due_at == due_at,
                )
            )
        ).scalar_one()
        return dose_dict(dose)


def due_times(row: Any, day) -> list[datetime]:
    """This medicine's due times on ``day`` (a date in its own timezone), in UTC."""
    tz = ZoneInfo(row.timezone)
    out = []
    for t in row.times or []:
        hour, minute = (int(x) for x in str(t).split(":"))
        out.append(
            datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz).astimezone(
                UTC
            )
        )
    return out


# --- the card's halves (called from services/care/cards.py) ----------------


async def card_args(organization_id: int, arguments: dict[str, Any]) -> dict[str, Any]:
    from api.services.care import circle

    person = int(arguments.get("person_user_id") or 0)
    async with db_client.async_session() as session:
        row = await _own(
            session, organization_id, person, int(arguments.get("medicine_id") or 0)
        )
        if row.state not in (AWAITING, PAUSED):
            raise CareError("That reminder is already running.")
    members = await circle.alertable_members(
        organization_id, person, list(row.alert_member_ids or [])
    )
    return {
        "medicine_id": row.id,
        "person_user_id": person,
        "label": row.label,
        "times": list(row.times or []),
        "timezone": row.timezone,
        "language": row.language,
        "phone": row.phone,
        "alert_member_ids": [m["id"] for m in members],
        "alert_names": [m["name"] for m in members],
    }


async def activate(
    organization_id: int, args: dict[str, Any], version: str | None
) -> str:
    """The person confirmed this exact reminder: it starts ringing."""
    async with db_client.async_session() as session:
        row = await _own(
            session,
            organization_id,
            int(args["person_user_id"]),
            int(args["medicine_id"]),
        )
        if row.state not in (AWAITING, PAUSED):
            raise CareError("That reminder is already running.")
        same = (
            row.label == args.get("label")
            and list(row.times or []) == list(args.get("times") or [])
            and row.phone == args.get("phone")
            and row.language == args.get("language")
            and list(row.alert_member_ids or [])
            == list(args.get("alert_member_ids") or [])
        )
        if not same:
            raise CareError("This changed after it was shown to you. Ask again.")
        row.state = ACTIVE
        row.approved_version = version
        row.updated_at = _now()
        await session.commit()
    told = (
        f" If a call is missed, {', '.join(args['alert_names'])} will be told."
        if args.get("alert_names")
        else ""
    )
    return (
        f"Reminder calls are on for {args['label']}: every day at "
        f"{say_times(list(args['times']))}, in "
        f"{LANGUAGE_NAMES.get(args['language'], args['language'])}.{told}"
    )


async def deactivate(organization_id: int, args: dict[str, Any]) -> None:
    async with db_client.async_session() as session:
        row = await _own(
            session,
            organization_id,
            int(args["person_user_id"]),
            int(args["medicine_id"]),
        )
        row.state = PAUSED
        row.updated_at = _now()
        await session.commit()


async def not_started(organization_id: int, args: dict[str, Any]) -> None:
    """The person said no to the card: a new reminder never starts; a paused
    one stays paused."""
    async with db_client.async_session() as session:
        row = await _own(
            session,
            organization_id,
            int(args.get("person_user_id") or 0),
            int(args.get("medicine_id") or 0),
        )
        if row.state == AWAITING:
            row.state = DECLINED
            row.updated_at = _now()
            await session.commit()
