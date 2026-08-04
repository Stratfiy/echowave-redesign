"""The per-run CSV that campaign, workflow and usage reports all share.

The column layout lives in one place so every endpoint emits the same shape,
and these tests pin the part of it that a tender depends on: the Language
column. ``workflow_runs.language`` has been populated by the pipeline all
along, and the CSV simply did not select it — so language distribution, which
the Telangana tender §10 requires, was underivable from the export a customer
is handed.
"""

import csv
import io
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from api.db.models import (
    CampaignModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.reports.run_report import build_run_report_csv


def _row(**overrides):
    """One report row, shaped like what the DB clients select."""
    base = dict(
        id=1,
        workflow_id=2,
        definition_id=3,
        campaign_id=4,
        created_at=datetime(2026, 3, 10, 6, 0, tzinfo=UTC),
        language="te-IN",
        initial_context={"phone_number": "+919876543210"},
        gathered_context={"mapped_call_disposition": "XFER"},
        cost_info={},
        usage_info={"call_duration_seconds": "100"},
        public_access_token=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _parse(rows):
    return list(csv.reader(io.StringIO(build_run_report_csv(rows).getvalue())))


class TestTheLanguageColumn:
    def test_the_header_is_present(self):
        header = _parse([_row()])[0]
        assert "Language" in header

    def test_the_language_is_written_against_its_run(self):
        parsed = _parse([_row(language="te-IN")])
        header, row = parsed[0], parsed[1]
        assert row[header.index("Language")] == "te-IN"

    def test_a_run_with_no_language_recorded_is_blank_not_none(self):
        """``None`` would be rendered as the string "None" and read as a
        language by anyone pivoting the file."""
        parsed = _parse([_row(language=None)])
        header, row = parsed[0], parsed[1]
        assert row[header.index("Language")] == ""

    def test_distribution_is_derivable_from_the_export(self):
        """The actual requirement. Given the CSV alone, a reader must be able
        to count calls by language without going back to the API."""
        parsed = _parse(
            [
                _row(id=1, language="te-IN"),
                _row(id=2, language="te-IN"),
                _row(id=3, language="hi-IN"),
            ]
        )
        header, rows = parsed[0], parsed[1:]
        column = header.index("Language")
        counts: dict[str, int] = {}
        for row in rows:
            counts[row[column]] = counts.get(row[column], 0) + 1
        assert counts == {"te-IN": 2, "hi-IN": 1}


class TestTheSharedLayout:
    def test_extracted_variables_sit_between_the_fixed_columns(self):
        """The layout is pre + per-campaign variables + post. Language belongs
        in the fixed prefix, so adding a variable cannot move it."""
        header = _parse(
            [_row(gathered_context={"extracted_variables": {"crop": "cotton"}})]
        )[0]
        assert header.index("Language") < header.index("crop")
        assert header.index("crop") < header.index("Transcript URL")

    def test_a_row_has_a_value_for_every_column(self):
        parsed = _parse(
            [_row(gathered_context={"extracted_variables": {"crop": "cotton"}})]
        )
        assert len(parsed[1]) == len(parsed[0])


@pytest.mark.asyncio
class TestTheQuerySelectsLanguage:
    """The column layout is only half of it: the DB clients select an explicit
    column list, so a missing column here renders as an empty cell rather than
    an error."""

    async def test_the_campaign_report_query_returns_language(
        self, async_session, db_session
    ):
        # db_session points the DB client at this test's transaction, so the
        # rows below are visible to a query that opens its own session.
        db_client = db_session

        user = UserModel(provider_id="user-csv")
        org = OrganizationModel(provider_id="org-csv", quota_decibyl_tokens=0)
        async_session.add_all([user, org])
        await async_session.flush()
        workflow = WorkflowModel(
            name="wf-csv",
            user_id=user.id,
            organization_id=org.id,
            workflow_definition={},
            template_context_variables={},
            call_disposition_codes={},
        )
        async_session.add(workflow)
        await async_session.flush()
        campaign = CampaignModel(
            name="c-csv",
            organization_id=org.id,
            workflow_id=workflow.id,
            created_by=user.id,
            source_type="csv",
            source_id="c.csv",
            state="running",
        )
        async_session.add(campaign)
        await async_session.flush()
        async_session.add(
            WorkflowRunModel(
                name="run",
                workflow_id=workflow.id,
                campaign_id=campaign.id,
                mode="twilio",
                usage_info={"call_duration_seconds": "100"},
                cost_info={},
                initial_context={},
                gathered_context={},
                is_completed=True,
                language="te-IN",
                created_at=datetime.now(UTC),
            )
        )
        await async_session.flush()

        rows = await db_client.get_completed_runs_for_report(campaign_id=campaign.id)
        assert [r.language for r in rows] == ["te-IN"]
