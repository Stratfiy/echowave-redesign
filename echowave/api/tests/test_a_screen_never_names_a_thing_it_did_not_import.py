"""Three screens returned 500 on the live account, one cause each: a name the
module never imported. ``timedelta`` in the organisation graph, ``db_client``
in connector activity, the whole SQL vocabulary in the call-intent report.
Every one of them dates from the commit that introduced it, because every
test of those endpoints mocked the function that held the mistake.

Two guards, so it cannot come back in either shape. The first runs each
formerly broken query for real against the test database, with nothing to
find, and expects the empty shape rather than a NameError. The second asks
ruff for undefined names across the whole API, which is what would have
caught all three in one line.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.db import db_client
from api.routes import connectors
from api.services.reports.call_intent import intent_breakdown

NOBODY = -1  # an organisation id no row will ever carry


class TestTheQueriesRunForReal:
    @pytest.mark.asyncio
    async def test_the_organisation_graph_draws_an_empty_business(self):
        data = await db_client.organisation_graph_edges(organization_id=NOBODY, days=90)
        assert [n["kind"] for n in data["nodes"]] == ["organisation"]
        assert data["edges"] == []

    @pytest.mark.asyncio
    async def test_the_call_intent_report_counts_no_calls(self):
        async with db_client.async_session() as session:
            rows = await intent_breakdown(
                session, organization_id=NOBODY, days=1, workflow_id=None
            )
        assert rows == []

    @pytest.mark.asyncio
    async def test_connector_activity_reaches_the_database_client(self):
        rows = [
            {"kind": "composio", "app": "gmail", "calls": 4, "errors": 1, "avg_ms": 812}
        ]
        with patch.object(
            connectors.db_client,
            "app_interaction_summary",
            new=AsyncMock(return_value=rows),
        ) as summary:
            response = await connectors.connector_activity(
                days=30, user=SimpleNamespace(selected_organization_id=7)
            )
        summary.assert_awaited_once_with(organization_id=7, days=30)
        assert [a.app for a in response.apps] == ["gmail"]
        assert response.apps[0].errors == 1


class TestNoModuleNamesAThingItNeverImported:
    def test_ruff_finds_no_undefined_names(self):
        pytest.importorskip("ruff", reason="ruff is not installed here")
        api_dir = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                str(api_dir),
                "--select",
                "F821",
                "--output-format",
                "concise",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
