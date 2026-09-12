"""The sentence under an agent's name.

Every case here is one an owner would read on the home screen and act on, so
the tests are written as claims about what the screen says rather than about
what the function returns.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.routes.team import team_status
from api.services.workflow import status_lines


def _activity(**kwargs):
    base = {
        "calls": 0,
        "answered": 0,
        "dialled": 0,
        "outcomes": 0,
        "failures": 0,
        "last_run_at": None,
        "last_action": None,
    }
    base.update(kwargs)
    return base


class TestSilenceIsAState:
    def test_an_agent_that_did_nothing_still_gets_a_sentence(self):
        """A blank line reads as a broken screen. "Nothing today" reads as
        nothing today, which is information."""
        line = status_lines.status_line(None)
        assert line["text"] == "Nothing today"
        assert line["tone"] == status_lines.TONE_IDLE

    def test_a_shorter_window_says_so(self):
        line = status_lines.status_line(_activity(), hours=6)
        assert line["text"] == "Nothing in the last 6h"


class TestPausedWins:
    def test_a_paused_agent_never_advertises_todays_calls(self):
        """An agent that is not taking calls showing "9 calls today" invites
        the owner to believe it is still on."""
        line = status_lines.status_line(
            _activity(calls=9, dialled=9, answered=6), is_live=False
        )
        assert line["text"] == "Paused — not taking calls"
        assert line["tone"] == status_lines.TONE_PAUSED


class TestCounts:
    def test_calls_and_answers_read_as_one_sentence(self):
        line = status_lines.status_line(_activity(calls=9, dialled=9, answered=6))
        assert line["text"] == "9 calls, 6 answered"
        assert line["tone"] == status_lines.TONE_WORKING

    def test_a_browser_test_never_claims_an_answer_rate(self):
        """Only a carrier can report an answer. Counting WebRTC tests would
        print "3 calls, 0 answered" over a morning that went fine."""
        line = status_lines.status_line(_activity(calls=3, dialled=0, answered=0))
        assert line["text"] == "3 calls"

    def test_one_call_is_singular(self):
        line = status_lines.status_line(_activity(calls=1, dialled=1, answered=1))
        assert line["text"] == "1 call, 1 answered"


class TestTheVerbIsNeverInvented:
    def test_a_booking_tool_gives_the_line_the_word_bookings(self):
        line = status_lines.status_line(
            _activity(
                calls=9,
                dialled=9,
                answered=6,
                outcomes=4,
                last_action={
                    "name": "book_appointment",
                    "app": "googlecalendar",
                    "status": "success",
                    "at": datetime.now(UTC),
                },
            )
        )
        assert line["text"] == "9 calls, 6 answered, 4 bookings"

    def test_an_unknown_tool_says_handled_rather_than_guessing(self):
        """ "Booked" is specific and sometimes false; "handled" is vague and
        always true. The screen may not claim an outcome it cannot name."""
        line = status_lines.status_line(
            _activity(
                calls=2,
                outcomes=2,
                last_action={
                    "name": "lookup_price",
                    "app": "http",
                    "status": "success",
                    "at": datetime.now(UTC),
                },
            )
        )
        assert line["text"] == "2 calls, 2 handled"

    def test_a_tool_name_is_never_printed_as_a_slug(self):
        assert status_lines.humanise_tool_name("book_appointment") == "Book appointment"
        assert status_lines.humanise_tool_name("sendWhatsApp") == "Send Whats App"
        assert status_lines.humanise_tool_name(None) == "Action"


class TestAttention:
    def test_a_run_of_failures_changes_the_tone(self):
        line = status_lines.status_line(
            _activity(calls=5, dialled=5, answered=5, failures=4)
        )
        assert "4 failures" in line["text"]
        assert line["tone"] == status_lines.TONE_ATTENTION

    def test_one_failed_action_is_called_out_without_crying_wolf(self):
        line = status_lines.status_line(
            _activity(
                calls=5,
                dialled=5,
                answered=5,
                failures=1,
                last_action={
                    "name": "book_appointment",
                    "app": "googlecalendar",
                    "status": "error",
                    "at": datetime.now(UTC),
                },
            )
        )
        assert line["text"].endswith("last action failed")
        assert line["tone"] == status_lines.TONE_ATTENTION


class TestTheTeamEndpoint:
    def _user(self, org=42):
        return SimpleNamespace(selected_organization_id=org)

    def _workflow(self, id, name, is_live=True):
        return SimpleNamespace(
            id=id, name=name, is_live=is_live, workflow_uuid=f"uuid-{id}"
        )

    @pytest.mark.asyncio
    async def test_the_agent_needing_attention_is_listed_first(self):
        """An owner should not have to scroll to find the agent that has
        stopped filing bookings."""
        with (
            patch(
                "api.routes.team.db_client.get_all_workflows_for_listing",
                AsyncMock(
                    return_value=[
                        self._workflow(1, "Quiet"),
                        self._workflow(2, "Busy"),
                        self._workflow(3, "Broken"),
                    ]
                ),
            ),
            patch(
                "api.routes.team.db_client.agent_activity",
                AsyncMock(
                    return_value={
                        2: _activity(calls=9, dialled=9, answered=6),
                        3: _activity(calls=4, dialled=4, answered=4, failures=5),
                    }
                ),
            ),
        ):
            response = await team_status(hours=24, user=self._user())

        assert [m.name for m in response.members] == ["Broken", "Busy", "Quiet"]
        assert response.members[0].tone == status_lines.TONE_ATTENTION
        assert response.members[2].status == "Nothing today"

    @pytest.mark.asyncio
    async def test_the_organization_is_taken_from_the_session(self):
        with (
            patch(
                "api.routes.team.db_client.get_all_workflows_for_listing",
                AsyncMock(return_value=[]),
            ) as listing,
            patch(
                "api.routes.team.db_client.agent_activity", AsyncMock(return_value={})
            ) as activity,
        ):
            await team_status(hours=24, user=self._user(org=7))

        assert listing.await_args.kwargs["organization_id"] == 7
        assert activity.await_args.kwargs["organization_id"] == 7

    @pytest.mark.asyncio
    async def test_a_session_with_no_organization_is_refused(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as raised:
            await team_status(hours=24, user=self._user(org=None))
        assert raised.value.status_code == 400
