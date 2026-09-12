"""Which calendars count as busy, and why it is not "all of them".

An appointment kept on a second calendar was invisible to both the conflict
check and availability, so a slot already taken could be offered and booked
again. The practitioner finds out when two people arrive for one chair.

The fix could not be "read every calendar on the account", and that is what
most of this file is about. Reading everything is right for a solo dentist
whose own appointments sit on a personal calendar, and wrong for a two-doctor
clinic: merging both doctors reports the clinic full when only one of them is
booked, which is the same failure as refusing a free slot -- the bug fixed
earlier today, wearing a different hat. Nothing in a calendar list says which
case an account is, so the operator chooses, and an account that chooses
nothing behaves exactly as it did before.
"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.integrations.google_calendar import availability
from api.services.integrations.google_calendar.client import _find_conflicting_event
from api.services.integrations.google_calendar.oauth import (
    ConnectionStatus,
    _busy_ids,
)


def _status(calendar_id="work@example.com", busy=()):
    return ConnectionStatus(
        connected=True,
        connected_email="clinic@example.com",
        calendar_id=calendar_id,
        updated_at=None,
        busy_calendar_ids=tuple(busy),
    )


def _response(status_code, items):
    return SimpleNamespace(
        status_code=status_code, json=lambda: {"items": items}, text=""
    )


def _event(start="11:00", end="11:30"):
    return {
        "status": "confirmed",
        "summary": "Mrs Lakshmi",
        "start": {"dateTime": f"2026-09-14T{start}:00+05:30"},
        "end": {"dateTime": f"2026-09-14T{end}:00+05:30"},
    }


class TestTheReadSet:
    def test_an_account_that_chose_nothing_reads_exactly_what_it_read_before(self):
        """The default is the whole safety of this change. Every existing
        connection migrates with an empty list."""
        assert _status().read_calendar_ids == ("work@example.com",)

    def test_no_calendar_configured_still_means_primary(self):
        assert _status(calendar_id=None).read_calendar_ids == ("primary",)

    def test_the_booking_calendar_is_always_read(self):
        """A read set built from the extras alone would miss the calendar the
        bookings are actually on, which is worse than the bug."""
        ids = _status(busy=["personal@example.com"]).read_calendar_ids
        assert ids[0] == "work@example.com"
        assert "personal@example.com" in ids

    def test_the_booking_calendar_is_not_read_twice(self):
        ids = _status(
            busy=["work@example.com", "personal@example.com"]
        ).read_calendar_ids
        assert ids == ("work@example.com", "personal@example.com")

    @pytest.mark.parametrize(
        "junk", [None, [], [None], [""], ["  "], [42], {}, 7, "personal"]
    )
    def test_a_hand_edited_row_cannot_take_bookings_down(self, junk):
        """A JSON column, so the database guarantees nothing about the shape.

        The case that caught me writing this: a row holding the bare string
        "personal" iterates into eight single-character ids, and the read path
        then asks Google about a calendar called "p" -- eight failed requests
        before an agent can answer a caller. A non-list is refused rather than
        iterated.
        """
        assert _busy_ids(junk) == ()

    def test_a_real_list_survives_it(self):
        """The sanitiser must not be so strict that it eats the feature."""
        assert _busy_ids([" personal@x.com ", "", 3, "other@x.com"]) == (
            "personal@x.com",
            "other@x.com",
        )


class TestTheConflictCheck:
    @pytest.mark.asyncio
    async def test_a_clash_on_a_second_calendar_now_blocks(self):
        """The bug. The first calendar is clear, the second is not, and before
        this the booking went through."""
        calls = []

        async def get(url, **kwargs):
            calls.append(url)
            return _response(200, [_event()] if "personal" in url else [])

        with patch(
            "api.services.integrations.google_calendar.client.httpx.AsyncClient"
        ) as cls:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=get)
            cls.return_value.__aenter__.return_value = client
            conflict = await _find_conflicting_event(
                "token",
                ("work@example.com", "personal@example.com"),
                "2026-09-14T11:00:00",
                "2026-09-14T11:30:00",
            )

        assert conflict is not None
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_it_stops_at_the_first_conflict(self):
        """One is enough to refuse, and the rest is latency a caller waits
        through."""
        calls = []

        async def get(url, **kwargs):
            calls.append(url)
            return _response(200, [_event()])

        with patch(
            "api.services.integrations.google_calendar.client.httpx.AsyncClient"
        ) as cls:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=get)
            cls.return_value.__aenter__.return_value = client
            await _find_conflicting_event(
                "token",
                ("a@x.com", "b@x.com", "c@x.com"),
                "2026-09-14T11:00:00",
                "2026-09-14T11:30:00",
            )

        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_one_unreadable_calendar_does_not_stop_the_others(self):
        """A calendar unshared after being configured is a 404. Treating that
        as "the check failed" would quietly stop checking the calendar that
        still works."""

        async def get(url, **kwargs):
            if "gone" in url:
                return _response(404, [])
            return _response(200, [_event()])

        with patch(
            "api.services.integrations.google_calendar.client.httpx.AsyncClient"
        ) as cls:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=get)
            cls.return_value.__aenter__.return_value = client
            conflict = await _find_conflicting_event(
                "token",
                ("gone@x.com", "work@x.com"),
                "2026-09-14T11:00:00",
                "2026-09-14T11:30:00",
            )

        assert conflict is not None

    @pytest.mark.asyncio
    async def test_a_single_id_is_not_iterated_character_by_character(self):
        """A caller holding one string must not have Google asked about a
        calendar named "p"."""
        calls = []

        async def get(url, **kwargs):
            calls.append(url)
            return _response(200, [])

        with patch(
            "api.services.integrations.google_calendar.client.httpx.AsyncClient"
        ) as cls:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=get)
            cls.return_value.__aenter__.return_value = client
            await _find_conflicting_event(
                "token", "primary", "2026-09-14T11:00:00", "2026-09-14T11:30:00"
            )

        assert len(calls) == 1


class TestAvailabilityAgrees:
    """The two must never disagree, or the agent offers a slot and then fails
    to book it -- worse than never offering it."""

    @pytest.mark.asyncio
    async def test_events_from_every_calendar_remove_slots(self):
        async def get(url, **kwargs):
            if "personal" in url:
                return _response(200, [_event("12:00", "12:30")])
            return _response(200, [_event("11:00", "11:30")])

        with patch(
            "api.services.integrations.google_calendar.availability.httpx.AsyncClient"
        ) as cls:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=get)
            cls.return_value.__aenter__.return_value = client
            items = await availability.busy_events(
                "token",
                ("work@example.com", "personal@example.com"),
                date(2026, 9, 14),
                "Asia/Kolkata",
            )

        spans = availability._busy_spans(
            items, availability._zone("Asia/Kolkata"), date(2026, 9, 14)
        )
        free = availability.free_slots(
            open_windows=[(570, 780)], busy=spans, duration_minutes=30
        )
        assert "11:00" not in free
        assert "12:00" not in free  # the second calendar's appointment

    @pytest.mark.asyncio
    async def test_a_partial_read_is_a_failure_not_a_free_day(self):
        """Slots computed from two of three calendars is exactly how a booked
        chair gets offered. "I could not check" is the honest answer."""

        async def get(url, **kwargs):
            if "gone" in url:
                return _response(404, [])
            return _response(200, [])

        with patch(
            "api.services.integrations.google_calendar.availability.httpx.AsyncClient"
        ) as cls:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=get)
            cls.return_value.__aenter__.return_value = client
            items = await availability.busy_events(
                "token", ("work@x.com", "gone@x.com"), date(2026, 9, 14), "Asia/Kolkata"
            )

        assert items is None

    @pytest.mark.asyncio
    async def test_a_single_id_still_works(self):
        with patch(
            "api.services.integrations.google_calendar.availability.httpx.AsyncClient"
        ) as cls:
            client = AsyncMock()
            client.get = AsyncMock(return_value=_response(200, [_event()]))
            cls.return_value.__aenter__.return_value = client
            items = await availability.busy_events(
                "token", "primary", date(2026, 9, 14), "Asia/Kolkata"
            )

        assert items and len(items) == 1
        assert client.get.await_count == 1
