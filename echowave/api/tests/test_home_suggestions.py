"""The chips under the composer on the home screen.

Each test is a claim about what an owner sees, because the failure mode here
is not a crash -- it is a screen full of plausible suggestions that are not
true of this account, which teaches people to stop reading them.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.routes.team import team_home
from api.services.workflow import home_suggestions


def _member(**kwargs):
    base = {
        "workflow_id": 1,
        "name": "Front Desk",
        "is_live": True,
        "calls": 0,
        "answered": 0,
        "outcomes": 0,
        "failures": 0,
        "tone": "idle",
    }
    base.update(kwargs)
    return base


class TestNothingIsInvented:
    def test_an_empty_account_gets_the_only_hardcoded_chips_there_are(self):
        """With no agents there is nothing true to say about the account, so
        the openers describe jobs instead."""
        chips = home_suggestions.build(members=[], failures_by_app={})
        assert [c["text"] for c in chips] == list(home_suggestions.STARTERS)
        assert all(c["action"] == home_suggestions.ACTION_PROMPT for c in chips)

    def test_a_healthy_account_is_not_padded_with_filler(self):
        """One chip that is worth clicking beats four that are decoration."""
        chips = home_suggestions.build(
            members=[_member(calls=9, answered=6, outcomes=4, tone="working")],
            failures_by_app={},
        )
        assert len(chips) == 1
        assert chips[0]["kind"] == "hire"

    def test_never_more_than_four(self):
        chips = home_suggestions.build(
            members=[
                _member(workflow_id=1, name="A", calls=9, outcomes=0),
                _member(workflow_id=2, name="B", is_live=False),
            ],
            failures_by_app={"googlecalendar": 9, "gmail": 5},
            unreturned_missed_calls=3,
        )
        assert len(chips) <= home_suggestions.MAX_SUGGESTIONS


class TestNoChipEverFiresAnAction:
    def test_every_chip_only_fills_the_composer_or_navigates(self):
        """A chip that silently starts calling a hundred customers is how an
        account is lost. There is no third action."""
        chips = home_suggestions.build(
            members=[
                _member(workflow_id=1, name="A", calls=9, outcomes=0),
                _member(workflow_id=2, name="B", is_live=False),
            ],
            failures_by_app={"googlecalendar": 9},
            unreturned_missed_calls=3,
        )
        assert chips
        for chip in chips:
            assert chip["action"] in {
                home_suggestions.ACTION_PROMPT,
                home_suggestions.ACTION_LINK,
            }
            if chip["action"] == home_suggestions.ACTION_PROMPT:
                assert chip["prompt"]
            else:
                assert chip["href"]


class TestTheChipsComeFromRealState:
    def test_a_failing_connector_is_the_loudest_chip(self):
        chips = home_suggestions.build(
            members=[_member(calls=9, outcomes=4)],
            failures_by_app={"googlecalendar": 6},
        )
        assert chips[0]["kind"] == "connector_failing"
        assert "Googlecalendar failed 6 times" in chips[0]["text"]

    def test_one_stray_failure_is_not_worth_interrupting_anybody(self):
        chips = home_suggestions.build(
            members=[_member(calls=9, outcomes=4)],
            failures_by_app={"googlecalendar": 1},
        )
        assert all(c["kind"] != "connector_failing" for c in chips)

    def test_an_agent_answering_calls_and_filing_nothing_is_called_out(self):
        """The most expensive silent failure in the product: the calls are
        billed, the owner believes it works, and no record is created."""
        chips = home_suggestions.build(
            members=[_member(name="Front Desk", calls=12, outcomes=0)],
            failures_by_app={},
        )
        assert chips[0]["kind"] == "no_outcomes"
        assert "took 12 calls and filed nothing" in chips[0]["text"]
        assert chips[0]["href"] == "/workflow/1/settings"

    def test_a_handful_of_calls_with_no_outcome_is_not_yet_a_problem(self):
        chips = home_suggestions.build(
            members=[_member(calls=2, outcomes=0)], failures_by_app={}
        )
        assert all(c["kind"] != "no_outcomes" for c in chips)

    def test_a_paused_agent_is_offered_back(self):
        chips = home_suggestions.build(
            members=[_member(name="Night Line", is_live=False, tone="paused")],
            failures_by_app={},
        )
        assert chips[0]["kind"] == "paused"
        assert chips[0]["href"] == "/workflow/1"

    def test_callers_nobody_rang_back_are_counted_in_the_singular_too(self):
        chips = home_suggestions.build(
            members=[_member(calls=9, outcomes=4)],
            failures_by_app={},
            unreturned_missed_calls=1,
        )
        assert "1 caller rang" in chips[0]["text"]


class TestTheHomeEndpoint:
    def _user(self, org=42):
        return SimpleNamespace(selected_organization_id=org)

    @pytest.mark.asyncio
    async def test_the_headline_adds_up_what_the_team_did(self):
        workflows = [
            SimpleNamespace(id=1, name="A", is_live=True, workflow_uuid="a"),
            SimpleNamespace(id=2, name="B", is_live=False, workflow_uuid="b"),
        ]
        activity = {
            1: {
                "calls": 9,
                "answered": 6,
                "dialled": 9,
                "outcomes": 4,
                "failures": 0,
                "last_run_at": None,
                "last_action": None,
            }
        }
        with (
            patch(
                "api.routes.team.db_client.get_all_workflows_for_listing",
                AsyncMock(return_value=workflows),
            ),
            patch(
                "api.routes.team.db_client.agent_activity",
                AsyncMock(return_value=activity),
            ),
            patch(
                "api.routes.team.db_client.app_interaction_summary",
                AsyncMock(return_value=[]),
            ),
            patch(
                "api.routes.team.db_client.unreturned_missed_call_count",
                AsyncMock(return_value=0),
            ),
        ):
            response = await team_home(hours=24, user=self._user())

        assert response.headline.agents == 2
        assert response.headline.live == 1
        assert response.headline.calls == 9
        assert response.headline.answered == 6
        assert response.headline.outcomes == 4
        assert response.suggestions

    @pytest.mark.asyncio
    async def test_a_session_with_no_organization_is_refused(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as raised:
            await team_home(hours=24, user=self._user(org=None))
        assert raised.value.status_code == 400
