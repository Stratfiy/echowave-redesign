"""A person's own preferences: language, timezone, voice and summary time.

Handoff 31 item 3 and 30: workspace preferences
(services/organization_preferences.py) are the team's -- the workspace's
timezone runs its schedules -- so a person choosing their own must never
write there. These live in ``member_preferences``, keyed by the person, and
nothing here takes an organisation or another person's id: the route passes
the signed-in user, and that is the only row it can read or write.

Saves follow the design's save contract (dirty -> saving -> confirmed): the
client sends the ``revision`` it read; an older one is a conflict and the
answer carries the stored value, so the screen can show both rather than
silently overwrite. Only the fields a request names change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, available_timezones

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from api.db import db_client
from api.db.controls_models import MemberPreferencesModel
from api.services import features

FLAG = "member_preferences"

#: The languages a person can pick. BCP 47 tags; the launch's Indian
#: languages plus English. Which of them voice supports is the voice
#: stream's to say; this is the person's choice of language to be spoken to.
LANGUAGES = (
    "en-IN",
    "hi-IN",
    "bn-IN",
    "ta-IN",
    "te-IN",
    "kn-IN",
    "ml-IN",
    "mr-IN",
    "gu-IN",
    "pa-IN",
    "od-IN",
    "ur-IN",
    "as-IN",
    "en",
)
FIELDS = ("language", "timezone", "voice", "summary_time")
#: The rest of a person's own settings (launch stream `settings`,
#: SETTINGS.md). Same row, same revision; accepted only while
#: ``settings_shell`` is on (the route checks).
SETTINGS_FIELDS = (
    "preferred_name",
    "explanation_language",
    "response_length",
    "custom_instructions",
    "memory_enabled",
    "speaking_speed",
    "captions",
    "auto_detect_language",
)
ALL_FIELDS = FIELDS + SETTINGS_FIELDS
RESPONSE_LENGTHS = ("short", "balanced", "detailed")
#: Long enough for a paragraph of standing instructions; a longer one is
#: refused with the limit, never silently cut (screen 18).
MAX_INSTRUCTIONS = 1500
MAX_NAME = 80
SPEED_RANGE = (0.5, 2.0)
_BOOLEAN_FIELDS = ("memory_enabled", "captions", "auto_detect_language")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_VOICE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")


def enabled() -> bool:
    return features.is_on(FLAG)


class PreferenceInvalid(ValueError):
    pass


@dataclass
class Conflict(Exception):
    """The save named a revision older than the stored one."""

    stored: dict[str, Any]


def _empty(user_id: int) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "language": None,
        "timezone": None,
        "voice": None,
        "summary_time": None,
        **{key: None for key in SETTINGS_FIELDS},
        "onboarding_absorbed_at": None,
        "revision": 0,
        "updated_at": None,
    }


def _as_dict(row: MemberPreferencesModel | None, user_id: int) -> dict[str, Any]:
    if row is None:
        return _empty(user_id)
    return {
        "user_id": row.user_id,
        "language": row.language,
        "timezone": row.timezone,
        "voice": row.voice,
        "summary_time": row.summary_time,
        **{key: getattr(row, key) for key in SETTINGS_FIELDS},
        "onboarding_absorbed_at": (
            row.onboarding_absorbed_at.isoformat()
            if row.onboarding_absorbed_at
            else None
        ),
        "revision": row.revision,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def validate(changes: dict[str, Any]) -> dict[str, Any]:
    """The changes, checked. None clears a field. Raises PreferenceInvalid."""
    out: dict[str, Any] = {}
    for key, value in changes.items():
        if key not in ALL_FIELDS:
            raise PreferenceInvalid(f"{key} is not a personal preference")
        if value is None:
            out[key] = None
            continue
        if key in _BOOLEAN_FIELDS:
            if not isinstance(value, bool):
                raise PreferenceInvalid(f"{key} must be on or off")
            out[key] = value
            continue
        if key == "speaking_speed":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PreferenceInvalid("Speaking speed is a number like 1.0.")
            low, high = SPEED_RANGE
            if not low <= float(value) <= high:
                raise PreferenceInvalid(f"Speaking speed goes from {low} to {high}.")
            out[key] = round(float(value), 2)
            continue
        if not isinstance(value, str):
            raise PreferenceInvalid(f"{key} must be text")
        value = value.strip()
        if key == "language" and value not in LANGUAGES:
            raise PreferenceInvalid("That language is not offered yet.")
        if key == "timezone":
            if value not in available_timezones():
                raise PreferenceInvalid("That is not a timezone we know.")
            ZoneInfo(value)
        if key == "summary_time" and not _TIME.match(value):
            raise PreferenceInvalid("A summary time looks like 09:00.")
        if key == "voice" and not _VOICE.match(value):
            raise PreferenceInvalid("That is not a voice id.")
        if key == "explanation_language" and value not in LANGUAGES:
            raise PreferenceInvalid("That language is not offered yet.")
        if key == "response_length" and value not in RESPONSE_LENGTHS:
            raise PreferenceInvalid("Choose short, balanced or detailed.")
        if key == "custom_instructions":
            # Never cut: a person's instruction trimmed without a word is a
            # different instruction (screen 18).
            if len(value) > MAX_INSTRUCTIONS:
                raise PreferenceInvalid(
                    f"Instructions can be up to {MAX_INSTRUCTIONS} characters; "
                    f"these are {len(value)}."
                )
            value = value or None
        if key == "preferred_name":
            if len(value) > MAX_NAME:
                raise PreferenceInvalid(f"A name can be up to {MAX_NAME} characters.")
            value = value or None
        out[key] = value
    return out


async def get(user_id: int) -> dict[str, Any]:
    async with db_client.async_session() as session:
        row = await session.get(MemberPreferencesModel, user_id)
        return _as_dict(row, user_id)


async def timezone_of(user_id: int | None) -> str | None:
    """The person's own timezone, or None. Never raises."""
    if not user_id:
        return None
    try:
        return (await get(int(user_id))).get("timezone")
    except Exception:  # noqa: BLE001 - a display helper
        return None


async def save(
    user_id: int, changes: dict[str, Any], *, revision: int
) -> dict[str, Any]:
    """Apply ``changes`` if ``revision`` is the stored one. Returns the new
    row. Raises Conflict with the stored row when it is not."""
    clean = validate(changes)
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        if revision == 0:
            # Nothing stored yet, as far as the client knows: create, or
            # conflict if somebody (another tab) created it first.
            inserted = (
                await session.execute(
                    insert(MemberPreferencesModel)
                    .values(user_id=user_id, revision=1, updated_at=now, **clean)
                    .on_conflict_do_nothing(index_elements=["user_id"])
                    .returning(MemberPreferencesModel.user_id)
                )
            ).first()
            if inserted is None:
                await session.rollback()
                row = await session.get(MemberPreferencesModel, user_id)
                raise Conflict(stored=_as_dict(row, user_id))
        else:
            hit = (
                await session.execute(
                    update(MemberPreferencesModel)
                    .where(
                        MemberPreferencesModel.user_id == user_id,
                        MemberPreferencesModel.revision == revision,
                    )
                    .values(
                        revision=MemberPreferencesModel.revision + 1,
                        updated_at=now,
                        **clean,
                    )
                    .returning(MemberPreferencesModel.user_id)
                )
            ).first()
            if hit is None:
                await session.rollback()
                row = await session.get(MemberPreferencesModel, user_id)
                raise Conflict(stored=_as_dict(row, user_id))
        await session.commit()
        row = (
            await session.execute(
                select(MemberPreferencesModel)
                .where(MemberPreferencesModel.user_id == user_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        return _as_dict(row, user_id)
