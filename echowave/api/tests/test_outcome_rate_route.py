"""The outcome-rate endpoint, and the ownership check in front of it.

An id in a URL path proves nothing. Without the check, asking for somebody
else's workflow id would answer with their call volumes and how well their
agent is doing, which is competitive intelligence rather than a metrics bug.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes.workflow_outcomes import outcome_rate


def _user(org=42):
    return SimpleNamespace(selected_organization_id=org)


class TestOwnership:
    @pytest.mark.asyncio
    async def test_another_accounts_workflow_is_not_measured(self):
        """get_workflow is organization-scoped, so a workflow that comes back
        None is either deleted or somebody else's. Both are 404."""
        with (
            patch(
                "api.routes.workflow_outcomes.db_client.get_workflow",
                AsyncMock(return_value=None),
            ),
            patch(
                "api.routes.workflow_outcomes.db_client.outcomes_by_version",
                AsyncMock(),
            ) as report,
        ):
            with pytest.raises(HTTPException) as raised:
                await outcome_rate(workflow_id=7, days=30, user=_user())

        assert raised.value.status_code == 404
        report.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_organization_is_taken_from_the_session_not_the_request(self):
        with (
            patch(
                "api.routes.workflow_outcomes.db_client.get_workflow",
                AsyncMock(return_value=SimpleNamespace(id=7)),
            ) as lookup,
            patch(
                "api.routes.workflow_outcomes.db_client.outcomes_by_version",
                AsyncMock(return_value=[]),
            ) as report,
        ):
            await outcome_rate(workflow_id=7, days=30, user=_user(org=42))

        assert lookup.await_args.kwargs["organization_id"] == 42
        assert report.await_args.kwargs["organization_id"] == 42

    @pytest.mark.asyncio
    async def test_a_session_with_no_organization_is_refused(self):
        with pytest.raises(HTTPException) as raised:
            await outcome_rate(workflow_id=7, days=30, user=_user(org=None))
        assert raised.value.status_code == 400


class TestShape:
    @pytest.mark.asyncio
    async def test_a_version_nobody_has_run_reports_no_rate_rather_than_zero(self):
        """Printing 0% beside an unrun version reads as "this version fails",
        which is a different claim from "nobody has run it"."""
        rows = [
            {
                "definition_id": 13,
                "version_number": 13,
                "published_at": None,
                "calls": 0,
                "calls_with_outcome": 0,
                "outcome_rate": None,
            }
        ]
        with (
            patch(
                "api.routes.workflow_outcomes.db_client.get_workflow",
                AsyncMock(return_value=SimpleNamespace(id=7)),
            ),
            patch(
                "api.routes.workflow_outcomes.db_client.outcomes_by_version",
                AsyncMock(return_value=rows),
            ),
        ):
            response = await outcome_rate(workflow_id=7, days=30, user=_user())

        assert response.versions[0].outcome_rate is None
        assert response.versions[0].calls == 0
