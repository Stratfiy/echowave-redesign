"""The call-log view that answers questions a table of rows cannot.

A list of runs tells you which calls happened. It does not tell you what
fraction of them connected, how long they ran, which agent carries the volume,
or what hour the phone actually rings — and those are the four things that
decide whether an agent is working.

Two properties are load-bearing and tested first:

* the account comes from the **authenticated user**, never from the request
* an empty range reports **null**, not zero, for every derived ratio — "no
  calls" and "calls that averaged nothing" are different findings and only one
  of them is a problem
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from api.db.models import (
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)


async def _account(session, slug: str):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}")
    session.add_all([org, user])
    await session.flush()
    user.selected_organization_id = org.id
    await session.flush()
    return org, user


async def _agent(session, org, user, name="agent"):
    workflow = WorkflowModel(
        name=name, organization_id=org.id, user_id=user.id, status="active"
    )
    session.add(workflow)
    await session.flush()
    return workflow


async def _call(
    session,
    workflow,
    *,
    when=None,
    seconds=60,
    answered=True,
    direction="outbound",
    disposition=None,
    charged_paise=0,
    usage_info=None,
):
    when = when or datetime.now(UTC) - timedelta(days=1)
    run = WorkflowRunModel(
        name=f"run-{workflow.id}",
        workflow_id=workflow.id,
        mode="plivo",
        call_type=direction,
        is_completed=True,
        created_at=when,
        answered_at=when if answered else None,
        billable_seconds=seconds,
        total_charged_paise=charged_paise,
        gathered_context=(
            {"mapped_call_disposition": disposition} if disposition else {}
        ),
        usage_info=usage_info or {},
    )
    session.add(run)
    await session.flush()
    return run


def _client(user):
    from api.app import app
    from api.services.auth.depends import get_user

    @asynccontextmanager
    async def _ctx():
        async def _override():
            return user

        app.dependency_overrides[get_user] = _override
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield client
        finally:
            app.dependency_overrides.pop(get_user, None)

    return _ctx()


async def _fetch(user, **params):
    async with _client(user) as client:
        response = await client.get(
            "/api/v1/organizations/usage/calls", params=params or None
        )
    assert response.status_code == 200, response.text
    return response.json()


class TestTenantIsolation:
    async def test_another_orgs_id_in_the_query_is_ignored(
        self, db_session, async_session
    ):
        """If this route honoured an organization_id, every customer could read
        every other customer's call log shape."""
        _mine, my_user = await _account(async_session, "calls-mine")
        theirs, their_user = await _account(async_session, "calls-theirs")
        await _call(async_session, await _agent(async_session, theirs, their_user))

        body = await _fetch(my_user, organization_id=theirs.id)

        assert body["totals"]["calls"] == 0

    async def test_only_my_calls_are_counted(self, db_session, async_session):
        mine, my_user = await _account(async_session, "calls-only-mine")
        theirs, their_user = await _account(async_session, "calls-only-theirs")

        await _call(async_session, await _agent(async_session, mine, my_user))
        await _call(async_session, await _agent(async_session, theirs, their_user))
        await _call(async_session, await _agent(async_session, theirs, their_user))

        body = await _fetch(my_user)

        assert body["totals"]["calls"] == 1

    async def test_our_cost_and_margin_are_not_in_the_response(
        self, db_session, async_session
    ):
        """The daily series is a staff query and carries what the vendors
        charged us. Passing it through publishes our markup on every account —
        and no UI has to render a field for it to be readable in the JSON."""
        org, user = await _account(async_session, "calls-margin")
        await _call(
            async_session,
            await _agent(async_session, org, user),
            charged_paise=5000,
        )

        body = await _fetch(user)

        assert body["daily"], "expected a zero-filled daily series"
        for row in body["daily"]:
            assert "provider_cost_paise" not in row
            assert "margin_paise" not in row

    async def test_no_organization_selected_is_a_400(self, db_session, async_session):
        _org, user = await _account(async_session, "calls-unselected")
        user.selected_organization_id = None
        await async_session.flush()

        async with _client(user) as client:
            response = await client.get("/api/v1/organizations/usage/calls")

        assert response.status_code == 400


class TestHeadlineFigures:
    async def test_answer_rate_counts_connected_calls(self, db_session, async_session):
        org, user = await _account(async_session, "calls-answer")
        agent = await _agent(async_session, org, user)
        await _call(async_session, agent, answered=True)
        await _call(async_session, agent, answered=True)
        await _call(async_session, agent, answered=False)
        await _call(async_session, agent, answered=False)

        totals = (await _fetch(user))["totals"]

        assert totals["calls"] == 4
        assert totals["answered"] == 2
        assert totals["answer_rate"] == 0.5

    async def test_average_duration_is_over_all_calls(self, db_session, async_session):
        org, user = await _account(async_session, "calls-average")
        agent = await _agent(async_session, org, user)
        await _call(async_session, agent, seconds=30)
        await _call(async_session, agent, seconds=90)

        totals = (await _fetch(user))["totals"]

        assert totals["billable_seconds"] == 120
        assert totals["average_seconds"] == 60.0

    async def test_an_empty_range_reports_null_not_zero(
        self, db_session, async_session
    ):
        """A zero average would read as "every call was instant", which is a
        very different alarm from "there were no calls"."""
        _org, user = await _account(async_session, "calls-empty")

        totals = (await _fetch(user))["totals"]

        assert totals["calls"] == 0
        assert totals["average_seconds"] is None
        assert totals["answer_rate"] is None

    async def test_charged_total_is_carried(self, db_session, async_session):
        org, user = await _account(async_session, "calls-charged")
        agent = await _agent(async_session, org, user)
        await _call(async_session, agent, charged_paise=1200)
        await _call(async_session, agent, charged_paise=800)

        assert (await _fetch(user))["totals"]["charged_paise"] == 2000


class TestBreakdowns:
    async def test_disposition_split(self, db_session, async_session):
        org, user = await _account(async_session, "calls-disp")
        agent = await _agent(async_session, org, user)
        await _call(async_session, agent, disposition="XFER")
        await _call(async_session, agent, disposition="XFER")
        await _call(async_session, agent, disposition="DNC")

        by_disposition = {
            row["disposition"]: row["calls"]
            for row in (await _fetch(user))["by_disposition"]
        }

        assert by_disposition["XFER"] == 2
        assert by_disposition["DNC"] == 1

    async def test_a_call_with_no_disposition_stays_distinguishable(
        self, db_session, async_session
    ):
        """Folding these into an "unknown" bucket would hide the difference
        between a call that ended without one and a call the pipeline never
        finished writing."""
        org, user = await _account(async_session, "calls-nodisp")
        agent = await _agent(async_session, org, user)
        await _call(async_session, agent, disposition=None)

        rows = (await _fetch(user))["by_disposition"]

        assert rows == [{"disposition": None, "calls": 1, "billable_seconds": 60}]

    async def test_direction_split_carries_its_own_answer_count(
        self, db_session, async_session
    ):
        org, user = await _account(async_session, "calls-direction")
        agent = await _agent(async_session, org, user)
        await _call(async_session, agent, direction="inbound", answered=True)
        await _call(async_session, agent, direction="outbound", answered=True)
        await _call(async_session, agent, direction="outbound", answered=False)

        by_direction = {
            row["direction"]: row for row in (await _fetch(user))["by_direction"]
        }

        assert by_direction["inbound"]["calls"] == 1
        assert by_direction["outbound"]["calls"] == 2
        assert by_direction["outbound"]["answered"] == 1

    async def test_duration_buckets_keep_their_order_and_their_gaps(
        self, db_session, async_session
    ):
        """A histogram that drops empty bands and sorts by count is not a
        distribution — it rearranges itself as data arrives."""
        org, user = await _account(async_session, "calls-buckets")
        agent = await _agent(async_session, org, user)
        await _call(async_session, agent, seconds=10)
        await _call(async_session, agent, seconds=700)

        buckets = (await _fetch(user))["by_duration"]

        assert [b["bucket"] for b in buckets] == [
            "0–30s",
            "30–60s",
            "1–2m",
            "2–5m",
            "5–10m",
            "10m+",
        ]
        assert [b["calls"] for b in buckets] == [1, 0, 0, 0, 0, 1]

    async def test_bucket_boundaries_fall_in_the_upper_band(
        self, db_session, async_session
    ):
        """Exactly 30s belongs to 30–60s, not to 0–30s."""
        org, user = await _account(async_session, "calls-boundary")
        agent = await _agent(async_session, org, user)
        await _call(async_session, agent, seconds=30)

        buckets = {b["bucket"]: b["calls"] for b in (await _fetch(user))["by_duration"]}

        assert buckets["0–30s"] == 0
        assert buckets["30–60s"] == 1

    async def test_hour_of_day_is_ist_and_covers_the_whole_clock(
        self, db_session, async_session
    ):
        """IST is 5:30 off the hour, so bucketing in UTC would smear every
        peak across two hours."""
        org, user = await _account(async_session, "calls-hour")
        agent = await _agent(async_session, org, user)
        # 04:00 UTC is 09:30 IST.
        await _call(
            async_session,
            agent,
            when=datetime.now(UTC).replace(hour=4, minute=0) - timedelta(days=1),
        )

        by_hour = (await _fetch(user))["by_hour"]

        assert len(by_hour) == 24
        assert [row["hour"] for row in by_hour] == list(range(24))
        assert next(row for row in by_hour if row["calls"])["hour"] == 9

    async def test_agents_are_ranked_by_volume(self, db_session, async_session):
        org, user = await _account(async_session, "calls-agents")
        busy = await _agent(async_session, org, user, name="busy")
        quiet = await _agent(async_session, org, user, name="quiet")
        await _call(async_session, busy)
        await _call(async_session, busy)
        await _call(async_session, quiet)

        by_agent = (await _fetch(user))["by_agent"]

        assert [row["name"] for row in by_agent] == ["busy", "quiet"]
        assert by_agent[0]["calls"] == 2


class TestRange:
    async def test_calls_outside_the_window_are_excluded(
        self, db_session, async_session
    ):
        org, user = await _account(async_session, "calls-range")
        agent = await _agent(async_session, org, user)
        await _call(async_session, agent, when=datetime.now(UTC) - timedelta(days=2))
        await _call(async_session, agent, when=datetime.now(UTC) - timedelta(days=40))

        assert (await _fetch(user, days=7))["totals"]["calls"] == 1
        assert (await _fetch(user, days=90))["totals"]["calls"] == 2

    async def test_the_daily_series_spans_the_requested_window(
        self, db_session, async_session
    ):
        """Zero-filled, so a chart has no gaps to misread as missing data."""
        _org, user = await _account(async_session, "calls-daily")

        body = await _fetch(user, days=7)

        assert len(body["daily"]) == 7
        assert all(row["calls"] == 0 for row in body["daily"])


class TestOneBotsOwnNumbers:
    """The same route, narrowed to a single bot for its Analytics tab.

    A bot's owner asks "is this one working and what is it costing me", and
    until now the only answer was the account's total, which says nothing
    about the bot they are looking at.
    """

    async def test_every_figure_narrows_to_the_bot_asked_for(
        self, db_session, async_session
    ):
        org, user = await _account(async_session, "calls-one-bot")
        mine = await _agent(async_session, org, user, name="mine")
        other = await _agent(async_session, org, user, name="other")
        await _call(async_session, mine)
        await _call(async_session, other)
        await _call(async_session, other)

        assert (await _fetch(user))["totals"]["calls"] == 3
        assert (await _fetch(user, workflow_id=mine.id))["totals"]["calls"] == 1

    async def test_another_orgs_bot_id_answers_with_nothing(
        self, db_session, async_session
    ):
        """The id arrives in a URL. Scoping by it alone would answer for
        somebody else's bot, so the account condition stays on regardless."""
        _mine, my_user = await _account(async_session, "calls-bot-mine")
        theirs, their_user = await _account(async_session, "calls-bot-theirs")
        hidden = await _agent(async_session, theirs, their_user)
        await _call(async_session, hidden)

        body = await _fetch(my_user, workflow_id=hidden.id)

        assert body["totals"]["calls"] == 0
        assert body["tokens"]["total_tokens"] == 0

    async def test_the_daily_runs_series_spans_the_window_and_counts_this_bot(
        self, db_session, async_session
    ):
        """Its own series, off the runs. The account rollup that feeds
        `daily` has no bot dimension at all."""
        org, user = await _account(async_session, "calls-bot-daily")
        mine = await _agent(async_session, org, user, name="mine")
        other = await _agent(async_session, org, user, name="other")
        await _call(async_session, mine, when=datetime.now(UTC) - timedelta(days=1))
        await _call(async_session, other, when=datetime.now(UTC) - timedelta(days=1))

        body = await _fetch(user, days=7, workflow_id=mine.id)

        assert len(body["daily_runs"]) == 7
        assert sum(row["runs"] for row in body["daily_runs"]) == 1

    async def test_tokens_are_added_up_per_model(self, db_session, async_session):
        org, user = await _account(async_session, "calls-bot-tokens")
        mine = await _agent(async_session, org, user)
        await _call(
            async_session,
            mine,
            usage_info={
                "llm": {
                    "gpt-x": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "total_tokens": 120,
                    }
                }
            },
        )
        await _call(
            async_session,
            mine,
            usage_info={
                "llm": {
                    "gpt-x": {
                        "prompt_tokens": 50,
                        "completion_tokens": 10,
                        "total_tokens": 60,
                    },
                    "gpt-y": {
                        "prompt_tokens": 5,
                        "completion_tokens": 1,
                        "total_tokens": 6,
                    },
                }
            },
        )

        tokens = (await _fetch(user, workflow_id=mine.id))["tokens"]

        assert tokens["total_tokens"] == 186
        assert tokens["prompt_tokens"] == 155
        # Biggest consumer first: the question this answers is which model is
        # eating the budget, and a list in map order does not answer it.
        assert [row["model"] for row in tokens["by_model"]] == ["gpt-x", "gpt-y"]
        assert tokens["by_model"][0]["total_tokens"] == 180

    async def test_a_run_with_no_usage_recorded_is_not_an_error(
        self, db_session, async_session
    ):
        """Calls that finished before usage was written, and calls that never
        reached a model, both have nothing to add up."""
        org, user = await _account(async_session, "calls-bot-no-usage")
        mine = await _agent(async_session, org, user)
        await _call(async_session, mine)
        await _call(async_session, mine, usage_info={"llm": None})

        tokens = (await _fetch(user, workflow_id=mine.id))["tokens"]

        assert tokens["total_tokens"] == 0
        assert tokens["by_model"] == []

    async def test_the_account_wide_answer_carries_no_per_bot_blocks(
        self, db_session, async_session
    ):
        """Summing tokens account-wide would read every run the account has
        ever made, so the unscoped answer does not offer them at all rather
        than offering a figure that is quietly wrong or quietly expensive."""
        org, user = await _account(async_session, "calls-no-bot-blocks")
        await _call(async_session, await _agent(async_session, org, user))

        body = await _fetch(user)

        assert "tokens" not in body
        assert "daily_runs" not in body
