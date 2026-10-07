"""Who is looking at Today, in which workspace, and in which timezone.

Every read and write in this stream takes a :class:`Viewer`. The timezone is
the person's own (member preferences) before the workspace's: a reminder is
the person's, so "tomorrow at nine" is their nine. A workspace's routines keep
the workspace's zone (routes/routines.py) -- one member's zone must never move
a team's schedule (handoff 30).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from api.db import db_client
from api.enums import ORGANIZATION_ROLE_RANK, OrganizationRole

#: When neither the person nor the workspace has said: India, where the
#: launch is (the same default the calling window uses).
DEFAULT_TIMEZONE = "Asia/Kolkata"


class TodayError(ValueError):
    """A request that cannot be honoured; ``str(exc)`` is safe to show."""


class NotFound(TodayError):
    """Not here, or not this person's -- answered the same way, so an id
    cannot be used to learn that a colleague's item exists."""


@dataclass
class Conflict(Exception):
    """A save that named an older revision than the stored one."""

    stored: dict[str, Any]


@dataclass(frozen=True)
class Viewer:
    user_id: int
    organization_id: int
    zone_name: str
    is_admin: bool = False

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.zone_name)


def valid_zone(name: str | None) -> str | None:
    """``name`` if it is a real IANA zone, else None."""
    if not name or not isinstance(name, str):
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return name


async def zone_for(user_id: int | None, organization_id: int | None) -> str:
    """The person's zone, else the workspace's, else India. Never raises."""
    from api.services import member_preferences
    from api.services.organization_preferences import get_organization_preferences

    own = valid_zone(await member_preferences.timezone_of(user_id))
    if own:
        return own
    try:
        prefs = await get_organization_preferences(organization_id)
        workspace = valid_zone(getattr(prefs, "timezone", None))
    except Exception:  # noqa: BLE001 - a display default
        workspace = None
    return workspace or DEFAULT_TIMEZONE


async def is_admin(user_id: int, organization_id: int) -> bool:
    membership = await db_client.get_membership(user_id, organization_id)
    rank = ORGANIZATION_ROLE_RANK.get(membership.role if membership else "", -1)
    return rank >= ORGANIZATION_ROLE_RANK[OrganizationRole.ADMIN.value]


async def viewer_for(user: Any) -> Viewer:
    organization_id = getattr(user, "selected_organization_id", None)
    if not organization_id:
        raise TodayError("No workspace selected.")
    user_id = int(user.id)
    return Viewer(
        user_id=user_id,
        organization_id=int(organization_id),
        zone_name=await zone_for(user_id, int(organization_id)),
        is_admin=await is_admin(user_id, int(organization_id)),
    )


def day_bounds(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """Local midnight to the next local midnight, in UTC. DST-safe: each end
    is resolved in the zone on its own date."""
    start = datetime.combine(day, time(0, 0), tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def local_today(zone: ZoneInfo, now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(zone).date()


def full_local(moment: datetime, zone_name: str) -> str:
    """The full date and time a person confirms before anything is
    scheduled: "Thu 9 Oct 2026, 09:00 IST (Asia/Kolkata)"."""
    zone = ZoneInfo(zone_name)
    local = moment.astimezone(zone)
    abbreviation = local.strftime("%Z")
    return (
        f"{local.strftime('%a')} {local.day} {local.strftime('%b %Y, %H:%M')} "
        f"{abbreviation} ({zone_name})"
    )


def parse_hhmm(value: str | None) -> time | None:
    if not value or not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return None
    try:
        hour, minute = int(value[:2]), int(value[3:])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def at_local(day: date, hhmm: time, zone: ZoneInfo) -> datetime:
    """``day`` at ``hhmm`` in ``zone``, as UTC. A wall time skipped by a DST
    jump resolves forward (zoneinfo's fold=0 rule), predictably."""
    local = datetime.combine(day, hhmm, tzinfo=zone)
    # Normalise through UTC so a non-existent wall time lands on the real
    # instant after the gap rather than a time that never happens.
    return local.astimezone(UTC)
