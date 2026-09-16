"""What "today" means when somebody asks how the bots have been doing.

Decibyl told the same operator "7 calls today" and, minutes later, "15 calls
today". Both were true of the query that produced them and neither was true
of today: the window was ``now - 24 hours``, labelled "today". Three things
follow from that, and all three were reported as bugs.

* The number moves on its own. At 2:53pm the window starts at 2:53pm
  *yesterday*, so yesterday afternoon's calls are inside it and drop out one
  by one as the clock advances. Two answers minutes apart can disagree with
  nothing having happened.
* It never matches the screen the operator is looking at, or their own idea
  of today, which starts at midnight where they are -- not midnight UTC, and
  not this time yesterday.
* It manufactures contradictions. A bot paused an hour ago still carries the
  calls it answered this morning, and a reader handed "paused" next to "2
  calls" with no time anchor reasonably concludes the pause is not working.
  Decibyl did exactly that, in writing, to the founder.

So the window is computed once, here, with the label that honestly describes
it, and everything that reports on a span of work takes both. The label is
part of the window rather than the caller's to invent: a caller free to write
"today" over a rolling day is how this started.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger

#: What a day is counted in when the account has not said where it is. UTC is
#: the honest default -- it is the clock the rows are stored on -- and the
#: label below stops it being passed off as the operator's own midnight.
FALLBACK_ZONE = UTC


@dataclass(frozen=True)
class Window:
    """A span of work, and the words for it.

    ``since`` is an aware UTC instant, because that is what the run rows are
    compared against. ``label`` is what a sentence about this window may call
    it, and ``exact`` says whether it lines up with a calendar day the
    operator would recognise -- a caller that wants to be careful can say
    "in the last 24 hours" when it does not.
    """

    since: datetime
    label: str
    exact: bool = True


def _zone(name: str | None) -> tuple[ZoneInfo | type[UTC], bool]:
    """The account's zone, and whether we actually found it."""
    if not name:
        return FALLBACK_ZONE, False
    try:
        return ZoneInfo(name), True
    except (ZoneInfoNotFoundError, ValueError):
        # A stored zone we cannot load is a data problem, not a reason to
        # refuse to answer. Fall back and say the label is approximate.
        logger.warning("Unknown timezone on an organization: {}", name)
        return FALLBACK_ZONE, False


def day_so_far(timezone_name: str | None, *, now: datetime | None = None) -> Window:
    """Midnight where the operator is, to this instant.

    This is what "today" means to the person asking, and what the screens
    should agree with.
    """
    zone, known = _zone(timezone_name)
    moment = (now or datetime.now(UTC)).astimezone(zone)
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return Window(
        since=midnight.astimezone(UTC),
        label="today",
        exact=known,
    )


def last_days(days: int, *, now: datetime | None = None) -> Window:
    """A rolling span, named as the rolling span it is.

    Deliberately not "this week": a week has a first day, and an operator who
    reads "this week" on a Tuesday means since Monday, not since last Tuesday.
    """
    moment = now or datetime.now(UTC)
    unit = "day" if days == 1 else "days"
    return Window(
        since=moment - timedelta(days=days),
        label=f"in the last {days} {unit}",
        exact=True,
    )
