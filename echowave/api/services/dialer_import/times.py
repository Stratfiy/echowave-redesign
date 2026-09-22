"""Both dialers speak Indian time, as naive ``Y-m-d H:i:s`` strings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")
_FORMAT = "%Y-%m-%d %H:%M:%S"


def format_ist(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=IST)
    return moment.astimezone(IST).strftime(_FORMAT)


def parse_ist(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip()[:19], _FORMAT).replace(tzinfo=IST)
    except ValueError:
        return None


def yesterday_ist(now: datetime | None = None) -> tuple[datetime, datetime]:
    """The whole of the previous Indian day: what a nightly run imports."""
    now = (now or datetime.now(timezone.utc)).astimezone(IST)
    start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start.replace(hour=23, minute=59, second=59)


def last_two_days_ist(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Yesterday and the day before: the nightly window, so a call that
    failed on its first night is tried once more."""
    start, end = yesterday_ist(now)
    return start - timedelta(days=1), end
