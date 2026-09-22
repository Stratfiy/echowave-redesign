"""OP-5: a script's outside spend is capped per run, and an agent says what a
run costs, what it cannot exceed, and what its cap is.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api import constants
from api.enums import ToolCategory
from api.services.billing import events, run_estimate
from api.services.billing.credits import PAISE_PER_CREDIT
from api.services.sandbox import code_mode, jobs


def _web_row(max_pages=None):
    config = {"max_pages_per_run": max_pages} if max_pages else {}
    return SimpleNamespace(
        tool_uuid="web-1",
        name="Web search",
        description="",
        category=ToolCategory.WEB.value,
        definition={"type": "web", "config": config},
    )


def _gmail():
    return SimpleNamespace(
        tool_uuid="gm-1",
        name="Send email",
        description="",
        category=ToolCategory.COMPOSIO.value,
        definition={"config": {"toolkit": "gmail", "tool_slug": "GMAIL_SEND_EMAIL"}},
    )


class _FakeJobs:
    """A box that makes the calls it is told to, through the harness's
    bridge, and returns a done result."""

    def __init__(self, calls):
        self.calls = calls
        self.results = []

    async def run(self, *, tools, **kwargs):
        for name, args in self.calls:
            self.results.append(await tools(name, args))
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        return SimpleNamespace(
            status=jobs.DONE,
            exit_code=0,
            ok=True,
            calls=len(self.calls),
            started_at=now,
            finished_at=now,
            job_id=1,
            output="done",
            error="",
            as_result=lambda: {"status": "success", "output": "done"},
        )


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(constants, "DECIBYL_TOOLS_2026_09_ENABLED", True)
    monkeypatch.setattr(constants, "SCRIPT_EXTERNAL_SPEND_CAP_CREDITS", 50)


def _patched(fake, search=None, fetch=None):
    return (
        patch("api.services.sandbox.code_mode.jobs.run", fake.run),
        patch("api.services.sandbox.code_mode.allowed", AsyncMock(return_value=True)),
        patch(
            "api.services.billing.events.charge_in_own_session",
            AsyncMock(return_value=0),
        ),
        patch("api.services.workflow.agent_timeline.record", AsyncMock()),
        patch(
            "api.services.workflow.web_tools.search",
            search
            or AsyncMock(
                return_value={"status": "success", "results": [], "charged_paise": 60}
            ),
        ),
        patch(
            "api.services.workflow.web_tools.fetch",
            fetch
            or AsyncMock(
                return_value={"status": "success", "text": "x", "charged_paise": 50}
            ),
        ),
    )


class TestTheCap:
    def test_the_bots_own_figure_lowers_the_platforms_never_raises_it(self):
        assert code_mode.cap_paise_for(None) == 50 * PAISE_PER_CREDIT
        assert (
            code_mode.cap_paise_for({"script_spend_cap_credits": 10})
            == 10 * PAISE_PER_CREDIT
        )
        assert (
            code_mode.cap_paise_for({"script_spend_cap_credits": 500})
            == 50 * PAISE_PER_CREDIT
        )
        assert (
            code_mode.cap_paise_for({"script_spend_cap_credits": "10"})
            == 50 * PAISE_PER_CREDIT
        )
        assert (
            code_mode.cap_paise_for({"script_spend_cap_credits": True})
            == 50 * PAISE_PER_CREDIT
        )

    async def test_the_web_is_reachable_from_a_script_and_counted(self):
        fake = _FakeJobs([("web_search", {"query": "q"}), ("web_fetch", {"url": "u"})])
        p = _patched(fake)
        with p[0], p[1], p[2], p[3], p[4] as search, p[5] as fetch:
            out = await code_mode.run_for_bot(
                organization_id=1,
                code="print(1)",
                why="w",
                tools=[_web_row(max_pages=3), _gmail()],
                workflow_id=42,
                workflow_run_id=7,
                ref_id="7:script:c",
                spend_cap_paise=50 * PAISE_PER_CREDIT,
                run_key="run:7",
            )
        assert [r["status"] for r in fake.results] == ["success", "success"]
        assert "charged_paise" not in fake.results[0]
        assert search.call_args.kwargs["ref_id"] == "7:script:c:web_search:1"
        assert search.call_args.kwargs["workflow_id"] == 42
        f = fetch.call_args.kwargs
        assert f["run_key"] == "run:7" and f["max_pages"] == 3
        assert out["external_spend_credits"] == round(110 / PAISE_PER_CREDIT, 2)

    async def test_past_the_cap_a_metered_call_is_refused_and_said(self):
        fake = _FakeJobs(
            [
                ("web_search", {"query": "a"}),
                ("web_search", {"query": "b"}),
                ("web_search", {"query": "c"}),
            ]
        )
        search = AsyncMock(
            return_value={"status": "success", "results": [], "charged_paise": 60}
        )
        p = _patched(fake, search=search)
        with p[0], p[1], p[2], p[3], p[4], p[5]:
            out = await code_mode.run_for_bot(
                organization_id=1,
                code="x",
                why="w",
                tools=[_web_row()],
                ref_id="r",
                spend_cap_paise=3
                * PAISE_PER_CREDIT,  # 150 paise: two calls fit, a third would not
            )
        assert [r["status"] for r in fake.results] == ["success", "success", "refused"]
        assert "spend cap" in fake.results[2]["error"]
        assert search.await_count == 2
        assert "refused" in out["note"]

    async def test_without_the_web_tool_the_web_is_not_a_name_the_script_knows(self):
        fake = _FakeJobs([("web_search", {"query": "a"})])
        search = AsyncMock()
        p = _patched(fake, search=search)
        with p[0], p[1], p[2], p[3], p[4], p[5]:
            await code_mode.run_for_bot(
                organization_id=1, code="x", why="w", tools=[_gmail()], ref_id="r"
            )
        assert fake.results[0]["status"] == "error"
        assert "no tool called" in fake.results[0]["error"]
        search.assert_not_awaited()

    async def test_a_connected_app_call_stays_uncounted(self):
        name = min(code_mode._names(_gmail()))
        fake = _FakeJobs([(name, {"to": "x"})])
        p = _patched(fake)
        with (
            p[0],
            p[1],
            p[2],
            p[3],
            p[4],
            p[5],
            patch(
                "api.services.integrations.composio.client.execute_tool",
                AsyncMock(return_value={"status": "success", "data": {}}),
            ),
        ):
            out = await code_mode.run_for_bot(
                organization_id=1,
                code="x",
                why="w",
                tools=[_gmail(), _web_row()],
                ref_id="r",
            )
        assert fake.results[0]["status"] == "success"
        assert "external_spend_credits" not in out

    async def test_the_cap_is_read_from_the_bot_when_not_given(self):
        fake = _FakeJobs([])
        p = _patched(fake)
        wf = SimpleNamespace(workflow_configurations={"script_spend_cap_credits": 2})
        search = AsyncMock(
            return_value={"status": "success", "results": [], "charged_paise": 60}
        )
        fake.calls = [("web_search", {"query": "a"}), ("web_search", {"query": "b"})]
        with (
            p[0],
            p[1],
            p[2],
            p[3],
            p[5],
            patch("api.services.workflow.web_tools.search", search),
            patch(
                "api.services.sandbox.code_mode.db_client.get_workflow_by_id",
                AsyncMock(return_value=wf),
            ),
        ):
            await code_mode.run_for_bot(
                organization_id=1,
                code="x",
                why="w",
                tools=[_web_row()],
                workflow_id=9,
                ref_id="r",
            )
        # 2 credits = 100 paise: one 60-paise call fits, the second would pass 100.
        assert [r["status"] for r in fake.results] == ["success", "refused"]


class TestTheEstimate:
    def _shape(self, **kw):
        base = {
            "has_web": True,
            "connected_writes": 1,
            "is_routine": True,
            "runs_per_month": 22,
            "can_run_scripts": True,
        }
        base.update(kw)
        return run_estimate.Shape(**base)

    def test_lines_add_up_and_show_their_assumptions(self):
        est = run_estimate.estimate(
            self._shape(), items_per_run=10, search_pass_through_paise=10
        )
        by = {line.what: line for line in est.lines}
        assert by["Routine run"].credits == 2
        assert by["Text turns"].count == 20 and by["Text turns"].credits == 20
        assert by["Web searches (fee plus the vendor's price)"].credits_each == 1.2
        assert by["Page reads"].count == 20
        assert by["Connected-app calls (a send each)"].credits == 10
        assert est.per_run_credits == 2 + 20 + 12 + 20 + 10
        assert est.per_month_credits == est.per_run_credits * 22
        assert any("Assumes 10 item(s)" in n for n in est.notes)

    def test_the_hard_maximum_comes_from_the_caps(self):
        est = run_estimate.estimate(
            self._shape(pages_cap=5, script_cap_credits=7), items_per_run=100
        )
        by = {line.what: line for line in est.hard_maximum}
        assert by["Page reads, at the page cap"].count == 5
        assert by["Web searches, one per page allowed"].count == 5
        assert by["Inside a script, at its spend cap"].credits == 7
        assert (
            by["Text turns, at the run's turn cap"].count
            == run_estimate.MAX_TURNS_PER_RUN
        )
        # A hundred items a run cannot read more than the page cap.
        pages = {line.what: line for line in est.lines}["Page reads"]
        assert pages.count == 5

    def test_no_web_no_web_lines_and_no_vendor_note(self):
        est = run_estimate.estimate(
            self._shape(has_web=False), search_pass_through_paise=None
        )
        assert not any("Web" in line.what for line in est.lines)
        assert not any("No rate on file" in n for n in est.notes)

    def test_a_missing_rate_is_said_not_zeroed_silently(self):
        est = run_estimate.estimate(self._shape(), search_pass_through_paise=None)
        assert any("No rate on file" in n for n in est.notes)
        assert est.search_pass_through_credits == 0

    def test_the_shape_is_read_off_the_rows(self, monkeypatch):
        monkeypatch.setattr(events, "PREMIUM_CONNECTORS", frozenset({"salesforce"}))
        sf = SimpleNamespace(
            tool_uuid="sf",
            name="x",
            description="",
            category=ToolCategory.COMPOSIO.value,
            definition={
                "config": {
                    "toolkit": "salesforce",
                    "tool_slug": "SALESFORCE_CREATE_LEAD",
                }
            },
        )
        read = SimpleNamespace(
            tool_uuid="rd",
            name="x",
            description="",
            category=ToolCategory.COMPOSIO.value,
            definition={
                "config": {"toolkit": "gmail", "tool_slug": "GMAIL_FETCH_EMAILS"}
            },
        )
        shape = run_estimate.shape_of(
            {},
            [_web_row(max_pages=4), _gmail(), sf, read],
            configurations={"script_spend_cap_credits": 5},
            routines=[
                SimpleNamespace(cadence="weekdays", is_active=True),
                SimpleNamespace(cadence="weekly", is_active=False),
            ],
            can_run_scripts=True,
        )
        assert shape.has_web and shape.pages_cap == 4
        assert shape.connected_writes == 2 and shape.premium_writes == 1
        assert shape.script_cap_credits == 5
        assert shape.runs_per_month == 22 and shape.is_routine

    async def test_for_workflow_reports_the_cap_or_says_none(self):
        wf = SimpleNamespace(
            workflow_definition={"nodes": []}, workflow_configurations={}
        )
        db = SimpleNamespace(
            get_workflow=AsyncMock(return_value=wf),
            get_tools_by_uuids=AsyncMock(return_value=[]),
            routines_for_workflow=AsyncMock(return_value=[]),
        )
        with (
            patch("api.db.db_client", db),
            patch(
                "api.services.workflow.readiness.required_tool_uuids", lambda d, c: []
            ),
            patch(
                "api.services.sandbox.code_mode.allowed", AsyncMock(return_value=False)
            ),
            patch("api.services.billing.budgets.enabled", lambda: True),
            patch(
                "api.services.billing.budgets.policies_for", AsyncMock(return_value=[])
            ),
        ):
            out = await run_estimate.for_workflow(
                None, organization_id=1, workflow_id=5
            )
        assert out["caps"]["agent_budget"] is None
        assert out["caps"]["script_external_credits_per_run"] is None
        assert out["caps"]["pages_per_run"] == constants.WEB_FETCH_MAX_PAGES_PER_RUN
        assert out["estimate"]["per_run_credits"] == 20  # ten items, two turns each

    async def test_for_workflow_is_none_for_another_workspaces_agent(self):
        db = SimpleNamespace(get_workflow=AsyncMock(return_value=None))
        with patch("api.db.db_client", db):
            assert (
                await run_estimate.for_workflow(None, organization_id=1, workflow_id=5)
                is None
            )
