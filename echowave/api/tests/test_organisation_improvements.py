"""What the business is told it could do better.

The discipline these tests enforce is that a suggestion is a finding, not an
opinion. Every one names a count, none fires below a threshold, and none of
them changes anything by itself.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes.organisation import organisation
from api.services.organisation import improvements


def _gap(subject="not_understood", value="Do you open on Saturday?", times=12, id=1):
    return SimpleNamespace(
        id=id, subject_key=subject, value=value, times_seen=times, kind="gap"
    )


def _user(org=42):
    return SimpleNamespace(selected_organization_id=org)


class TestNothingIsSuggestedFromNoise:
    def test_one_odd_question_is_not_a_gap_in_the_business(self):
        assert improvements.from_gaps([_gap(times=1)]) == []
        assert improvements.from_gaps([_gap(times=2)]) == []

    def test_asked_enough_times_it_becomes_a_finding(self):
        found = improvements.from_gaps([_gap(times=improvements.MIN_TIMES_ASKED)])
        assert len(found) == 1
        assert "Asked 3 times" in found[0]["evidence"]

    def test_one_stray_connector_failure_says_nothing(self):
        assert improvements.from_failures({"googlecalendar": 1}) == []

    def test_a_run_of_them_is_urgent(self):
        found = improvements.from_failures({"googlecalendar": 6})
        assert found[0]["severity"] == improvements.SEVERITY_URGENT
        assert "6 failures" in found[0]["evidence"]


class TestEverySuggestionCarriesItsEvidence:
    def test_the_count_is_always_shown(self):
        """A suggestion whose evidence is hidden is indistinguishable from a
        guess, and a screen of guesses is one people stop opening."""
        found = improvements.from_gaps(
            [
                _gap(id=1, subject="not_understood"),
                _gap(id=2, subject="escalated", value="refund a damaged item"),
                _gap(id=3, subject="app_failed", value="googlecalendar"),
            ]
        )
        assert len(found) == 3
        for suggestion in found:
            assert suggestion["evidence"]
            assert suggestion["title"]

    def test_a_question_nobody_answered_asks_for_the_answer(self):
        found = improvements.from_gaps([_gap()])
        assert found[0]["action"] == improvements.ACTION_ANSWER
        assert "Do you open on Saturday?" in found[0]["prompt"]

    def test_a_broken_connector_opens_the_screen_that_fixes_it(self):
        found = improvements.from_gaps([_gap(subject="app_failed", value="shopify")])
        assert found[0]["action"] == improvements.ACTION_OPEN
        assert found[0]["href"] == "/integrations/apps"

    def test_nothing_applies_itself(self):
        """A suggestion opens a screen or fills the chat box. It never edits an
        agent, confirms a fact or reconnects anything."""
        found = improvements.from_gaps(
            [_gap(id=1), _gap(id=2, subject="app_failed", value="shopify")]
        ) + improvements.from_failures({"gmail": 9})
        for suggestion in found:
            assert suggestion["action"] in {
                improvements.ACTION_ANSWER,
                improvements.ACTION_OPEN,
            }


class TestDidTheLastChangeMakeItWorse:
    def _versions(self, *pairs):
        return [
            {
                "version_number": number,
                "calls": calls,
                "outcome_rate": rate,
                "definition_id": number,
                "published_at": None,
            }
            for number, calls, rate in pairs
        ]

    def test_a_real_drop_is_reported_with_both_numbers(self):
        """The one thing no competing platform can tell a customer: not "your
        agent changed" but "bookings fell a fifth after the change"."""
        found = improvements.from_versions(
            workflow_id=7,
            name="Front Desk",
            versions=self._versions((3, 100, 0.68), (4, 100, 0.45)),
        )
        assert len(found) == 1
        assert "68%" in found[0]["evidence"] and "45%" in found[0]["evidence"]
        assert found[0]["href"] == "/workflow/7"

    def test_a_few_points_of_movement_is_not_a_regression(self):
        assert (
            improvements.from_versions(
                workflow_id=7,
                name="Front Desk",
                versions=self._versions((3, 100, 0.68), (4, 100, 0.63)),
            )
            == []
        )

    def test_an_improvement_is_never_reported_as_a_regression(self):
        assert (
            improvements.from_versions(
                workflow_id=7,
                name="Front Desk",
                versions=self._versions((3, 100, 0.45), (4, 100, 0.68)),
            )
            == []
        )

    def test_a_version_pulled_after_four_calls_is_not_the_baseline(self):
        """Treating it as one would produce a confident sentence about noise."""
        assert (
            improvements.from_versions(
                workflow_id=7,
                name="Front Desk",
                versions=self._versions((3, 4, 1.0), (4, 100, 0.60)),
            )
            == []
        )

    def test_a_version_nobody_has_run_is_ignored_rather_than_scored_zero(self):
        assert (
            improvements.from_versions(
                workflow_id=7,
                name="Front Desk",
                versions=self._versions((3, 100, 0.68))
                + [
                    {
                        "version_number": 4,
                        "calls": 0,
                        "outcome_rate": None,
                        "definition_id": 4,
                        "published_at": None,
                    }
                ],
            )
            == []
        )


class TestRanking:
    def test_urgent_first_and_never_more_than_a_screenful(self):
        many = improvements.from_gaps([_gap(id=i, times=20) for i in range(20)])
        ranked = improvements.rank(many, limit=8)
        assert len(ranked) == 8
        assert ranked[0]["severity"] == improvements.SEVERITY_URGENT


class TestTheOrganisationScreen:
    @pytest.mark.asyncio
    async def test_an_archived_agent_still_appears_marked(self):
        """The whole argument of the screen: the agent switched off in March
        leaves its work behind, and hiding it would agree that knowledge
        belongs to whoever was holding it."""
        workflows = [
            SimpleNamespace(id=1, name="Front Desk", status="active"),
            SimpleNamespace(id=2, name="Old Night Line", status="archived"),
        ]
        with (
            patch(
                "api.routes.organisation.db_client.organisation_memory",
                AsyncMock(return_value=[]),
            ),
            patch(
                "api.routes.organisation.db_client.get_all_workflows_for_listing",
                AsyncMock(return_value=workflows),
            ),
            patch(
                "api.routes.organisation.db_client.agent_activity",
                AsyncMock(return_value={2: {"calls": 40, "outcomes": 30}}),
            ),
            patch(
                "api.routes.organisation.db_client.app_interaction_summary",
                AsyncMock(return_value=[]),
            ),
            patch(
                "api.routes.organisation.db_client.outcomes_by_version",
                AsyncMock(return_value=[]),
            ),
        ):
            response = await organisation(user=_user())

        by_name = {c.name: c for c in response.contributors}
        assert by_name["Old Night Line"].archived is True
        assert by_name["Old Night Line"].calls == 40
        assert by_name["Front Desk"].archived is False

    @pytest.mark.asyncio
    async def test_a_rejected_entry_is_not_shown_or_suggested_on(self):
        rows = [
            SimpleNamespace(
                id=1,
                kind="gap",
                subject_key="not_understood",
                key="k",
                value="Saturday hours?",
                status="rejected",
                times_seen=40,
                last_seen_at=None,
                source_run_id=None,
            )
        ]
        with (
            patch(
                "api.routes.organisation.db_client.organisation_memory",
                AsyncMock(return_value=rows),
            ),
            patch(
                "api.routes.organisation.db_client.get_all_workflows_for_listing",
                AsyncMock(return_value=[]),
            ),
            patch(
                "api.routes.organisation.db_client.agent_activity",
                AsyncMock(return_value={}),
            ),
            patch(
                "api.routes.organisation.db_client.app_interaction_summary",
                AsyncMock(return_value=[]),
            ),
        ):
            response = await organisation(user=_user())

        assert response.gaps == []
        assert response.suggestions == []

    @pytest.mark.asyncio
    async def test_a_session_with_no_organization_is_refused(self):
        with pytest.raises(HTTPException) as raised:
            await organisation(user=_user(org=None))
        assert raised.value.status_code == 400
