"""The door after sign-in (screen 02) and where a signed-in person lands.

Language, a confirmed timezone and an optional name, then one task into
Chat. Stored on the person (``user_onboarding``), never on the workspace, so
one member finishing it changes nothing for another.

``landing`` is the one answer to "where does this person go after sign-in",
read by the UI's three doors (``/``, ``/after-sign-in`` and
``getRedirectUrl``). With ``first_task_onboarding`` on: the onboarding
screen until it is done, then Chat -- never the build-an-agent journey,
whatever the agent count. Off: ``None``, and the doors keep their old rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from api.db import db_client
from api.db.shell_models import UserOnboardingModel
from api.services import features
from api.services.shell import languages

FLAG = "first_task_onboarding"

CHAT_PATH = "/overview"
ONBOARDING_PATH = "/welcome"


class Invalid(ValueError):
    """A field the person can fix; ``str(exc)`` is safe to show."""


@dataclass(frozen=True)
class State:
    language: str | None
    timezone: str | None
    timezone_confirmed: bool
    preferred_name: str | None
    completed: bool

    def as_dict(self) -> dict:
        return {
            "language": self.language,
            "timezone": self.timezone,
            "timezone_confirmed": self.timezone_confirmed,
            "preferred_name": self.preferred_name,
            "completed": self.completed,
        }


EMPTY = State(None, None, False, None, False)


def valid_timezone(name: str | None) -> bool:
    if not name or len(name) > 64:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _state(row: UserOnboardingModel | None) -> State:
    if row is None:
        return EMPTY
    return State(
        language=row.language,
        timezone=row.timezone,
        timezone_confirmed=row.timezone_confirmed_at is not None,
        preferred_name=row.preferred_name,
        completed=row.completed_at is not None,
    )


async def get(user_id: int) -> State:
    async with db_client.async_session() as session:
        row = await session.get(UserOnboardingModel, user_id)
        return _state(row)


async def save(
    user_id: int,
    *,
    language: str,
    timezone: str,
    timezone_confirmed: bool,
    preferred_name: str | None = None,
    complete: bool = False,
) -> State:
    """Write this person's answers. A timezone is stored only once they
    confirmed it: a detected zone saved silently is a guess with their name
    on it, and every reminder after it would fire at the wrong hour."""
    if not languages.is_supported(language):
        raise Invalid("Choose a language from the list.")
    if not timezone_confirmed:
        raise Invalid("Confirm your timezone before continuing.")
    if not valid_timezone(timezone):
        raise Invalid("Choose a timezone from the list.")
    name = (preferred_name or "").strip()[:80] or None
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        row = await session.get(UserOnboardingModel, user_id)
        if row is None:
            row = UserOnboardingModel(user_id=user_id, created_at=now)
            session.add(row)
        if row.timezone != timezone or row.timezone_confirmed_at is None:
            row.timezone_confirmed_at = now
        row.language = language
        row.timezone = timezone
        row.preferred_name = name
        if complete and row.completed_at is None:
            row.completed_at = now
        row.updated_at = now
        await session.commit()
        await session.refresh(row)
        return _state(row)


async def skip(user_id: int) -> State:
    """Finish without answering: every question here is optional except
    getting to Chat."""
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        row = await session.get(UserOnboardingModel, user_id)
        if row is None:
            row = UserOnboardingModel(user_id=user_id, created_at=now)
            session.add(row)
        if row.completed_at is None:
            row.completed_at = now
        row.updated_at = now
        await session.commit()
        await session.refresh(row)
        return _state(row)


async def landing(user_id: int, organization_id: int | None) -> str | None:
    """Where this person goes after sign-in, or None to keep the old rule."""
    if not features.is_on(FLAG, organization_id):
        return None
    state = await get(user_id)
    return CHAT_PATH if state.completed else ONBOARDING_PATH


async def completed_user_ids(user_ids: list[int]) -> set[int]:
    if not user_ids:
        return set()
    async with db_client.async_session() as session:
        rows = await session.execute(
            select(UserOnboardingModel.user_id).where(
                UserOnboardingModel.user_id.in_(user_ids),
                UserOnboardingModel.completed_at.is_not(None),
            )
        )
        return {r[0] for r in rows}
