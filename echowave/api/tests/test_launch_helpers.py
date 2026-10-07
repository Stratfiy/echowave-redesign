"""The five launch helpers and the builder (launch stream `agents`; handoff 6,
31.5; screen 06).

Done when: the five helpers and the builder are configurations over the one
Decibyl turn, each with an honest capability state the server enforces; a
helper only ever holds fewer tools than Automatic and is refused, not just
hidden, outside its list; switching a flag off restores today's behaviour;
and every row a person keeps is scoped to their workspace and private
until shared.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.services import features
from api.services.helpers import catalogue, states, turn
from api.services.helpers import tools as helper_tools
from api.services.workflow import connected_tools, decibyl

ALL_FLAGS = (
    "LAUNCH_HELPERS_ENABLED",
    "RESEARCH_REPORTS_ENABLED",
    "FOLLOW_UP_LEDGER_ENABLED",
    "TRADING_SUMMARIES_ENABLED",
    "DESCRIBE_BUILDER_ENABLED",
)
NEW = (
    "launch_helpers",
    "research_reports",
    "follow_up_ledger",
    "trading_summaries",
    "describe_builder",
)


@pytest.fixture
def helpers_on(monkeypatch):
    for name in ALL_FLAGS:
        monkeypatch.setattr(constants, name, True)


@pytest.fixture
def web_on(monkeypatch):
    monkeypatch.setattr(constants, "DECIBYL_TOOLS_2026_09_ENABLED", True)


def _tool(slug: str, toolkit: str, uuid: str = "t-1"):
    return SimpleNamespace(
        id=1,
        tool_uuid=uuid,
        name=f"{toolkit}: {slug.lower()}",
        description="d",
        category="composio",
        definition={
            "type": "composio",
            "config": {"tool_slug": slug, "toolkit": toolkit, "parameters": []},
        },
    )


@pytest.fixture
async def team(test_engine):
    """Owner ``a`` and member ``b`` in one workspace; ``c`` owns another."""
    run = uuid4().hex[:8]
    a, _ = await db_client.get_or_create_user_by_provider_id(f"help-a-{run}")
    b, _ = await db_client.get_or_create_user_by_provider_id(f"help-b-{run}")
    c, _ = await db_client.get_or_create_user_by_provider_id(f"help-c-{run}")
    org, _ = await db_client.get_or_create_organization_by_provider_id(
        f"help-org-{run}", a.id
    )
    other, _ = await db_client.get_or_create_organization_by_provider_id(
        f"help-other-{run}", c.id
    )
    await db_client.add_user_to_organization(a.id, org.id, "owner")
    await db_client.add_user_to_organization(b.id, org.id, "member")
    await db_client.add_user_to_organization(c.id, other.id, "owner")
    try:
        yield SimpleNamespace(a=a, b=b, c=c, org=org.id, other=other.id)
    finally:
        async with db_client.async_session() as session:
            for table in (
                "tracker_entries",
                "trackers",
                "commitments",
                "saved_reports",
                "helper_workspace_settings",
                "agent_events",
            ):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE organization_id = ANY(:o)"),
                    {"o": [org.id, other.id]},
                )
            await session.execute(
                text("DELETE FROM research_interests WHERE user_id = ANY(:u)"),
                {"u": [a.id, b.id, c.id]},
            )
            await session.commit()


def _as(user, org):
    return SimpleNamespace(id=user.id, selected_organization_id=org)


@asynccontextmanager
async def _client(user):
    from api.app import app
    from api.services.auth.depends import get_user

    app.dependency_overrides[get_user] = lambda: user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_user, None)


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


class TestFlags:
    def test_registered_described_and_off_by_default(self):
        for name in NEW:
            assert name in features.FLAGS
            assert features.DESCRIPTIONS.get(name)
            assert features.is_on(name) is False

    def test_the_ui_knows_every_flag(self):
        from pathlib import Path

        ts = (
            Path(__file__).resolve().parents[2] / "ui/src/lib/features.ts"
        ).read_text()
        for name in NEW:
            assert f'"{name}"' in ts


@pytest.mark.asyncio
class TestOffRestoresToday:
    async def test_every_route_is_a_404_while_off(self, team):
        user = _as(team.a, team.org)
        async with _client(user) as client:
            for path in (
                "/api/v1/helpers",
                "/api/v1/helpers/reports",
                "/api/v1/helpers/who-owes-me",
                "/api/v1/helpers/research/interests",
                "/api/v1/helpers/trackers",
            ):
                assert (await client.get(path)).status_code == 404, path

    async def test_a_helper_on_a_message_is_refused_while_off(self, team):
        async with _client(_as(team.a, team.org)) as client:
            response = await client.post(
                "/api/v1/timeline/message",
                json={"assistant": True, "text": "hi", "helper": "learning_guide"},
            )
        assert response.status_code == 409

    def test_no_new_tool_and_no_new_rule_while_off(self):
        names = {t["name"] for t in decibyl.office_tools(1)}
        assert not names & helper_tools.NAMES
        assert helper_tools.rules(1) == ""


# ---------------------------------------------------------------------------
# The catalogue: configurations over one runtime
# ---------------------------------------------------------------------------


class TestCatalogue:
    def test_the_five_in_the_handoffs_order(self):
        assert [catalogue.BY_KEY[k].name for k in catalogue.FIVE] == [
            "Inbox",
            "Research",
            "Follow-up",
            "Learning Guide",
            "Call and Appointment",
        ]

    def test_every_allowlisted_tool_is_one_decibyl_can_hold(self, helpers_on, web_on):
        """The allowlist's silent-absence guard: a renamed tool fails here."""
        from api.services.workflow import connected_tools as ct

        held = {t["name"] for t in decibyl.office_tools(1)} | {ct.LOAD_TOOL_NAME}
        for helper in catalogue.BY_KEY.values():
            missing = helper.tools - held
            assert not missing, (helper.key, missing)

    def test_every_new_tool_has_a_rule(self, helpers_on):
        rules = helper_tools.rules(1)
        for name in helper_tools.NAMES:
            assert f"- {name}:" in rules

    def test_each_helper_has_what_screen_06_shows(self):
        for helper in catalogue.BY_KEY.values():
            assert helper.job and helper.example and helper.permissions
            assert helper.boundary and helper.instructions


# ---------------------------------------------------------------------------
# Capability states
# ---------------------------------------------------------------------------


def _readings(**kw):
    base = dict(
        toolkits=set(),
        has_number=False,
        search_key=True,
        apps_configured=True,
        web_tools=True,
        flags={n: True for n in NEW},
    )
    base.update(kw)
    return states.Readings(**base)


class TestStates:
    def test_inbox_needs_mail_connected_then_is_available(self):
        inbox = catalogue.BY_KEY["inbox"]
        s = states.evaluate(inbox, _readings())
        assert s.state == states.NEEDS_SETUP and s.setup.kind == "connect"
        assert s.setup.app == "gmail"
        assert (
            states.evaluate(inbox, _readings(toolkits={"gmail"})).state == "available"
        )

    def test_a_failed_reading_is_unavailable_never_needs_setup(self):
        inbox = catalogue.BY_KEY["inbox"]
        s = states.evaluate(inbox, _readings(toolkits=None))
        assert s.state == states.UNAVAILABLE and "Could not check" in s.reason

    def test_research_without_a_search_key_needs_an_operator(self):
        s = states.evaluate(catalogue.BY_KEY["research"], _readings(search_key=False))
        assert s.state == states.NEEDS_SETUP and s.setup.kind == "operator"
        s = states.evaluate(catalogue.BY_KEY["research"], _readings(web_tools=False))
        assert s.state == states.UNAVAILABLE

    def test_call_and_appointment_needs_a_number_then_a_calendar(self):
        h = catalogue.BY_KEY["call_appointment"]
        assert states.evaluate(h, _readings()).setup.kind == "number"
        s = states.evaluate(h, _readings(has_number=True))
        assert s.state == states.NEEDS_SETUP and s.setup.app == "googlecalendar"
        assert (
            states.evaluate(
                h, _readings(has_number=True, toolkits={"googlecalendar"})
            ).state
            == states.AVAILABLE
        )

    def test_follow_up_is_available_and_says_what_sending_needs(self):
        s = states.evaluate(catalogue.BY_KEY["follow_up"], _readings())
        assert s.state == states.AVAILABLE and any("Gmail" in n for n in s.notes)
        off = _readings(flags={"follow_up_ledger": False})
        assert (
            states.evaluate(catalogue.BY_KEY["follow_up"], off).state == "unavailable"
        )

    def test_learning_guide_says_what_is_not_there_yet(self):
        s = states.evaluate(catalogue.BY_KEY["learning_guide"], _readings())
        assert s.state == states.AVAILABLE
        assert any("Learning" in n for n in s.notes)

    def test_a_workspace_switch_wins(self):
        s = states.evaluate(
            catalogue.BY_KEY["learning_guide"],
            _readings(workspace_off={"learning_guide"}),
        )
        assert s.state == states.DISABLED and "workspace" in s.reason


@pytest.mark.asyncio
class TestPickerRoute:
    async def test_all_five_and_the_builder_reach_the_picker(self, team, helpers_on):
        async with _client(_as(team.a, team.org)) as client:
            body = (await client.get("/api/v1/helpers")).json()
        assert [h["key"] for h in body["helpers"]] == list(catalogue.FIVE)
        assert body["builder"]["key"] == "builder"
        for h in body["helpers"]:
            assert h["state"] in (
                "available",
                "needs_setup",
                "disabled_by_policy",
                "unavailable",
            )
            assert h["example"] and h["permissions"]
        # The owner is an admin: skills and model inheritance are visible.
        assert body["can_manage"] is True
        assert body["helpers"][1]["advanced"]["skills"] == ["deep-research"]

    async def test_a_member_does_not_see_the_advanced_detail(self, team, helpers_on):
        async with _client(_as(team.b, team.org)) as client:
            body = (await client.get("/api/v1/helpers")).json()
        assert body["can_manage"] is False
        assert all(h["advanced"] is None for h in body["helpers"])

    async def test_admin_turns_one_off_for_everyone_member_cannot(
        self, team, helpers_on
    ):
        async with _client(_as(team.b, team.org)) as client:
            r = await client.put(
                "/api/v1/helpers/learning_guide/workspace", json={"enabled": False}
            )
            assert r.status_code == 403
        async with _client(_as(team.a, team.org)) as client:
            r = await client.put(
                "/api/v1/helpers/learning_guide/workspace", json={"enabled": False}
            )
            assert r.status_code == 200
        async with _client(_as(team.b, team.org)) as client:
            body = (await client.get("/api/v1/helpers")).json()
            guide = next(h for h in body["helpers"] if h["key"] == "learning_guide")
            assert guide["state"] == "disabled_by_policy"
            # And the server refuses it, whatever a screen shows.
            r = await client.post(
                "/api/v1/timeline/message",
                json={
                    "assistant": True,
                    "text": "teach me",
                    "helper": "learning_guide",
                },
            )
            assert r.status_code == 409
        # Another workspace is untouched.
        assert (await states.workspace_switches(team.other)) == {}


@pytest.mark.asyncio
class TestAHelperTurn:
    async def test_an_available_helper_is_recorded_and_carried_to_the_worker(
        self, team, helpers_on
    ):
        enqueue = AsyncMock()
        with patch("api.tasks.arq.enqueue_job", new=enqueue):
            async with _client(_as(team.a, team.org)) as client:
                r = await client.post(
                    "/api/v1/timeline/message",
                    json={
                        "assistant": True,
                        "text": "teach me fractions",
                        "helper": "learning_guide",
                        "thread_id": "t-help",
                    },
                )
        assert r.status_code == 200
        answer = next(
            c for c in enqueue.await_args_list if c.args[0] == "answer_decibyl_message"
        )
        assert answer.kwargs["helper"] == "learning_guide"
        rows = await db_client.agent_events(
            organization_id=team.org, assistant_thread=True, thread_id="t-help"
        )
        assert rows[0].payload["helper"] == "learning_guide"

    async def test_automatic_sends_no_helper_and_nothing_changes(
        self, team, helpers_on
    ):
        enqueue = AsyncMock()
        with patch("api.tasks.arq.enqueue_job", new=enqueue):
            async with _client(_as(team.a, team.org)) as client:
                r = await client.post(
                    "/api/v1/timeline/message",
                    json={"assistant": True, "text": "hello", "thread_id": "t-auto"},
                )
        assert r.status_code == 200
        answer = next(
            c for c in enqueue.await_args_list if c.args[0] == "answer_decibyl_message"
        )
        assert "helper" not in answer.kwargs

    async def test_a_needs_setup_helper_is_refused_with_its_reason(
        self, team, helpers_on
    ):
        async with _client(_as(team.a, team.org)) as client:
            r = await client.post(
                "/api/v1/timeline/message",
                json={"assistant": True, "text": "mail?", "helper": "inbox"},
            )
        assert r.status_code == 409


@pytest.mark.asyncio
class TestNarrowing:
    async def test_a_helper_holds_a_subset_never_more(self, helpers_on, web_on):
        gmail = _tool("GMAIL_FETCH_EMAILS", "gmail", "g1")
        hubspot = _tool("HUBSPOT_LIST_CONTACTS", "hubspot", "h1")
        with (
            patch.object(
                connected_tools,
                "list_for_organization",
                AsyncMock(return_value=[gmail, hubspot]),
            ),
            patch(
                "api.services.sandbox.code_mode.allowed",
                new=AsyncMock(return_value=False),
            ),
        ):
            everything = {t["name"] for t in await decibyl.tools_for(1, {})}
            with turn.running_as("inbox"):
                inbox = {t["name"] for t in await decibyl.tools_for(1, {})}
            with turn.running_as("research"):
                research = {t["name"] for t in await decibyl.tools_for(1, {})}
        assert inbox <= everything and research <= everything
        assert connected_tools.function_name(gmail) in inbox
        assert connected_tools.function_name(hubspot) not in inbox
        assert "web_search" in research and "web_search" not in inbox
        assert "propose_action" not in research
        assert "save_report" in research

    async def test_a_call_outside_the_list_is_refused_not_run(self, helpers_on, web_on):
        call = SimpleNamespace(id="c", name="web_search", arguments={"query": "x"})
        with (
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[])
            ),
            patch("api.services.workflow.web_tools.search", new=AsyncMock()) as search,
        ):
            with turn.running_as("inbox"):
                result = await decibyl._tool(1, call, 5)
        assert result["status"] == "refused" and result["helper_refused"]
        search.assert_not_awaited()
        assert decibyl._was_a_read(call, result)

    async def test_a_card_kind_outside_its_own_is_refused(self, helpers_on):
        call = SimpleNamespace(
            id="c", name="propose_action", arguments={"action": "forget_everything"}
        )
        with (
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[])
            ),
            patch("api.services.workflow.actions.propose", new=AsyncMock()) as propose,
        ):
            with turn.running_as("call_appointment"):
                result = await decibyl._tool(1, call, 5)
        assert result["status"] == "refused"
        propose.assert_not_awaited()

    async def test_the_helpers_instructions_reach_the_model(self, helpers_on):
        from api.services.agent_builder.client import ModelReply

        stream = AsyncMock(return_value=ModelReply(text="Let us start."))
        with (
            patch("api.services.agent_builder.client.stream", new=stream),
            patch.object(decibyl, "build_context", new=AsyncMock(return_value="")),
            patch.object(decibyl, "office_context", new=AsyncMock(return_value="")),
            patch.object(decibyl, "_history", new=AsyncMock(return_value=[])),
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[])
            ),
            patch(
                "api.services.agent_builder.settings.resolve_for_organization",
                new=AsyncMock(
                    return_value=SimpleNamespace(provider="p", model="m", api_key="k")
                ),
            ),
            patch(
                "api.services.routing.brain.auto_route",
                new=AsyncMock(return_value=None),
            ),
            patch.object(decibyl.agent_timeline, "record", new=AsyncMock()) as record,
            patch.object(decibyl.reply_draft, "clear", new=AsyncMock()),
        ):
            await decibyl.answer(1, "teach me", author_id=5, helper="learning_guide")
        system = stream.await_args.kwargs["system"]
        assert "Working as Learning Guide" in system
        assert "Never promise exam results" in system
        assert record.await_args.kwargs["payload"]["helper"] == "learning_guide"


# ---------------------------------------------------------------------------
# Research: reports, export, sharing, trading summaries
# ---------------------------------------------------------------------------


REPORT = {
    "title": "Home bakery GST",
    "question": "GST for a home bakery?",
    "summary": "Registration depends on turnover.",
    "findings": [
        {
            "statement": "The threshold is 40 lakh for goods.",
            "basis": "source",
            "sources": [1],
            "as_of": "2026-04-01",
        },
        {
            "statement": "Most home bakers will not need to register.",
            "basis": "inference",
        },
    ],
    "conflicts": [{"statement": "One blog says 20 lakh.", "sources": [2]}],
    "inaccessible": [{"url": "https://gst.gov.in/x", "reason": "Timed out"}],
    "sources": [
        {"url": "https://cbic.gov.in/gst", "title": "CBIC"},
        {"url": "https://blog.example/gst", "title": "A blog"},
    ],
}


class TestReportRules:
    def test_a_source_finding_must_cite_a_listed_source(self):
        from api.services.helpers import reports

        bad = {**REPORT, "findings": [{"statement": "x", "basis": "source"}]}
        with pytest.raises(reports.Invalid):
            reports.clean(bad)
        bad = {
            **REPORT,
            "findings": [{"statement": "x", "basis": "source", "sources": [9]}],
        }
        with pytest.raises(reports.Invalid):
            reports.clean(bad)

    def test_the_rendering_keeps_everything_apart(self):
        from api.services.helpers import reports

        body = reports.render(reports.clean(REPORT))
        assert (
            "- Source: The threshold is 40 lakh for goods. (as of 2026-04-01) [1]"
            in body
        )
        assert "- Inference: Most home bakers" in body
        assert (
            "## Where sources disagree" in body and "One blog says 20 lakh. [2]" in body
        )
        assert "https://gst.gov.in/x -- Timed out" in body
        assert "[1] CBIC -- https://cbic.gov.in/gst" in body

    def test_a_source_must_be_a_web_link(self):
        from api.services.helpers import reports

        with pytest.raises(reports.Invalid):
            reports.clean({**REPORT, "sources": [{"url": "javascript:alert(1)"}]})


@pytest.mark.asyncio
class TestSavedReports:
    async def test_the_tool_saves_and_the_export_matches_what_is_shown(
        self, team, helpers_on
    ):
        from api.services.workflow import agent_timeline

        with agent_timeline.in_thread("t-r"):
            result = await helper_tools.run(
                "save_report",
                organization_id=team.org,
                arguments=REPORT,
                author_id=team.a.id,
                thread_id="t-r",
                helper="research",
            )
        assert result["status"] == "success"
        uuid = result["report"]
        async with _client(_as(team.a, team.org)) as client:
            shown = (await client.get(f"/api/v1/helpers/reports/{uuid}")).json()
            md = await client.get(f"/api/v1/helpers/reports/{uuid}/export")
            html = await client.get(
                f"/api/v1/helpers/reports/{uuid}/export?format=html"
            )
            listed = (await client.get("/api/v1/helpers/reports")).json()
        assert md.text == shown["body"]
        assert md.headers["x-content-hash"] == shown["content_hash"]
        import hashlib

        assert hashlib.sha256(md.text.encode()).hexdigest() == shown["content_hash"]
        assert "The threshold is 40 lakh" in html.text
        assert [r["uuid"] for r in listed] == [uuid]
        # Every finding the screen shows is in the export.
        for f in shown["findings"]:
            assert f["statement"] in md.text
        # And the thread says it was saved, with a way to open it.
        rows = await db_client.agent_events(
            organization_id=team.org, assistant_thread=True, thread_id="t-r"
        )
        assert rows[0].payload["saved_report"]["uuid"] == uuid

    async def test_private_until_shared_never_across_workspaces(self, team, helpers_on):
        from api.services.helpers import reports

        row = await reports.save(
            reports.clean(REPORT), organization_id=team.org, user_id=team.a.id
        )
        path = f"/api/v1/helpers/reports/{row.uuid}"
        async with _client(_as(team.b, team.org)) as client:
            assert (await client.get(path)).status_code == 404
            assert (await client.get(path + "/export")).status_code == 404
            assert (await client.get("/api/v1/helpers/reports")).json() == []
            # Only the owner shares.
            r = await client.put(path + "/visibility", json={"visibility": "workspace"})
            assert r.status_code == 404
        async with _client(_as(team.a, team.org)) as client:
            r = await client.put(path + "/visibility", json={"visibility": "workspace"})
            assert r.json()["visibility"] == "workspace"
        async with _client(_as(team.b, team.org)) as client:
            seen = (await client.get(path)).json()
            assert seen["title"] == "Home bakery GST" and seen["mine"] is False
        async with _client(_as(team.c, team.other)) as client:
            assert (await client.get(path)).status_code == 404

    async def test_a_reply_is_kept_as_it_was_shown(self, team, helpers_on):
        from api.services.workflow import agent_timeline

        with agent_timeline.in_thread("t-k"):
            await agent_timeline.record(
                organization_id=team.org,
                kind="message",
                actor="human",
                summary="what is the GST threshold?",
                payload={"body": "what is the GST threshold?", "author_id": team.a.id},
                in_channel=False,
            )
            reply = await agent_timeline.record(
                organization_id=team.org,
                kind="message",
                actor="agent",
                summary="40 lakh",
                payload={"body": "40 lakh for goods (https://cbic.gov.in/gst)."},
                in_channel=False,
            )
        async with _client(_as(team.a, team.org)) as client:
            r = await client.post(
                "/api/v1/helpers/reports/from-reply", json={"event_id": reply}
            )
        body = r.json()
        assert body["question"] == "what is the GST threshold?"
        assert body["summary"] == "40 lakh for goods (https://cbic.gov.in/gst)."
        assert body["sources"][0]["url"] == "https://cbic.gov.in/gst"
        async with _client(_as(team.c, team.other)) as client:
            r = await client.post(
                "/api/v1/helpers/reports/from-reply", json={"event_id": reply}
            )
            assert r.status_code == 404


class TestInformationOnly:
    def test_advice_is_removed_and_facts_are_kept(self):
        from api.services.helpers import guard

        text = (
            "TCS fell 3% on Tuesday after results. You should buy the dip. "
            "Analysts at X raised their target to 4,200. Strong buy rating from me."
        )
        out = guard.scrub(text)
        assert "TCS fell 3%" in out and "raised their target" in out
        assert "You should buy" not in out and "Strong buy rating" not in out
        assert guard.REMOVED in out

    def test_a_research_trading_reply_always_carries_the_notice(self, helpers_on):
        from api.services.helpers import guard

        body = helper_tools.finish_reply(
            "Nifty closed up 0.4%. I recommend holding.",
            helper="research",
            request="trading summary for my stocks",
            organization_id=1,
        )
        assert guard.NOTICE in body and "I recommend holding" not in body
        # Not a trading turn, or not Research: untouched.
        assert (
            helper_tools.finish_reply(
                "Hold on.", helper="inbox", request="stocks", organization_id=1
            )
            == "Hold on."
        )

    @pytest.mark.asyncio
    async def test_a_saved_trading_summary_is_scrubbed(self, team, helpers_on):
        result = await helper_tools.run(
            "save_report",
            organization_id=team.org,
            arguments={
                **REPORT,
                "summary": "Infosys rose 2%. You should sell now.",
                "findings": [
                    {
                        "statement": "Infosys rose 2% on Monday.",
                        "basis": "source",
                        "sources": [1],
                    },
                    {"statement": "You should buy more.", "basis": "inference"},
                ],
            },
            author_id=team.a.id,
            thread_id=None,
            helper="research",
            request="trading summary of my stocks",
        )
        from api.services.helpers import guard, reports

        row = await reports.get_visible(
            result["report"], organization_id=team.org, user_id=team.a.id
        )
        assert row.kind == "trading_summary"
        assert "sell now" not in row.body and "buy more" not in row.body
        assert guard.NOTICE in row.body and "Infosys rose 2% on Monday." in row.body


@pytest.mark.asyncio
class TestInterests:
    async def test_saved_per_person_with_a_revision(self, team, helpers_on):
        async with _client(_as(team.a, team.org)) as client:
            first = (await client.get("/api/v1/helpers/research/interests")).json()
            assert first["interests"] == [] and first["revision"] == 0
            r = await client.put(
                "/api/v1/helpers/research/interests",
                json={"interests": [{"label": "tcs", "kind": "ticker"}], "revision": 0},
            )
            assert r.json()["interests"] == [{"label": "TCS", "kind": "ticker"}]
            stale = await client.put(
                "/api/v1/helpers/research/interests",
                json={"interests": [], "revision": 0},
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["stored"]["interests"][0]["label"] == "TCS"
        async with _client(_as(team.b, team.org)) as client:
            assert (await client.get("/api/v1/helpers/research/interests")).json()[
                "interests"
            ] == []

    async def test_a_summary_is_a_research_turn_in_the_thread(
        self, team, helpers_on, web_on
    ):
        from api.services.helpers import interests

        await interests.save(
            team.a.id, [{"label": "Nifty", "kind": "topic"}], revision=0
        )
        ask = AsyncMock(return_value=[])
        with (
            patch("api.services.workflow.decibyl.ask", new=ask),
            patch(
                "api.services.workflow.web_tools._search_key",
                new=AsyncMock(return_value="k"),
            ),
        ):
            async with _client(_as(team.a, team.org)) as client:
                r = await client.post(
                    "/api/v1/helpers/research/trading-summary",
                    json={"thread_id": "t-s"},
                )
        assert r.status_code == 200
        assert ask.await_args.kwargs["helper"] == "research"
        assert "Nifty" in ask.await_args.kwargs["text"]
        assert "No buy, sell or hold" in ask.await_args.kwargs["text"]


# ---------------------------------------------------------------------------
# Follow-up: commitments, who owes me, the follow-up card
# ---------------------------------------------------------------------------


async def _confirm_and_run(org: int, card_id: int, user_id: int) -> None:
    from api.services.workflow import actions

    with patch("api.tasks.arq.enqueue_job", new=AsyncMock()):
        await actions.settle(
            organization_id=org, event_id=card_id, verb="confirm", user_id=user_id
        )
    await actions.run(card_id, org)


@pytest.mark.asyncio
class TestCommitments:
    async def test_tracked_only_after_confirm_and_only_once(self, team, helpers_on):
        from api.services import acting
        from api.services.helpers import commitments

        with acting.acting_as(team.a.id), turn.running_as("follow_up"):
            proposed = await helper_tools.run(
                "track_commitment",
                organization_id=team.org,
                arguments={
                    "direction": "owed_to_me",
                    "counterparty": "Ravi",
                    "description": "invoice 12",
                    "amount": "4,800",
                    "due_on": "2026-10-10",
                },
                author_id=team.a.id,
                thread_id=None,
                helper="follow_up",
            )
        assert proposed["status"] == "proposed"
        card = await db_client.get_agent_event(
            proposed["card_id"], organization_id=team.org
        )
        assert (
            card.payload["label"]
            == "Track: Ravi owes you ₹4,800 for invoice 12 by 2026-10-10"
        )
        assert card.payload["helper"] == "follow_up"
        # Nothing tracked yet.
        assert (
            await commitments.list_visible(organization_id=team.org, user_id=team.a.id)
            == []
        )
        await _confirm_and_run(team.org, card.id, team.a.id)
        # A second run of the same card tracks nothing new.
        from api.services.workflow import actions

        await actions._execute(
            team.org,
            (
                await db_client.get_agent_event(card.id, organization_id=team.org)
            ).payload,
            event_id=card.id,
        )
        rows = await commitments.list_visible(
            organization_id=team.org, user_id=team.a.id
        )
        assert len(rows) == 1 and rows[0].amount_minor == 480000

        async with _client(_as(team.a, team.org)) as client:
            owed = (await client.get("/api/v1/helpers/who-owes-me")).json()
        assert owed["items"][0]["counterparty"] == "Ravi"
        assert owed["totals"] == [
            {"currency": "INR", "amount_minor": 480000, "amount": "₹4,800"}
        ]
        # Private: the teammate and the other workspace see nothing.
        async with _client(_as(team.b, team.org)) as client:
            assert (await client.get("/api/v1/helpers/who-owes-me")).json()[
                "items"
            ] == []
        async with _client(_as(team.c, team.other)) as client:
            assert (await client.get("/api/v1/helpers/who-owes-me")).json()[
                "items"
            ] == []

    async def test_settle_needs_the_revision_it_read_and_only_the_owner(
        self, team, helpers_on
    ):
        async with _client(_as(team.a, team.org)) as client:
            made = (
                await client.post(
                    "/api/v1/helpers/commitments",
                    json={
                        "counterparty": "Meena",
                        "description": "deposit",
                        "amount": "1000",
                    },
                )
            ).json()
            path = f"/api/v1/helpers/commitments/{made['uuid']}"
            ok = await client.patch(
                path, json={"revision": 1, "visibility": "workspace"}
            )
            assert ok.json()["revision"] == 2
            stale = await client.patch(path, json={"revision": 1, "status": "settled"})
            assert stale.status_code == 409
        async with _client(_as(team.b, team.org)) as client:
            seen = (await client.get("/api/v1/helpers/who-owes-me")).json()["items"]
            assert [i["counterparty"] for i in seen] == ["Meena"] and not seen[0][
                "mine"
            ]
            r = await client.patch(path, json={"revision": 2, "status": "settled"})
            assert r.status_code == 404
        async with _client(_as(team.a, team.org)) as client:
            done = await client.patch(path, json={"revision": 2, "status": "settled"})
            assert done.json()["status"] == "settled"
            assert (await client.get("/api/v1/helpers/who-owes-me")).json()[
                "items"
            ] == []

    async def test_the_follow_up_is_a_card_and_its_state_is_the_delivery_state(
        self, team, helpers_on
    ):
        from api.services.helpers import commitments
        from api.services.workflow import actions

        row = await commitments.create(
            commitments.clean({"counterparty": "Ravi", "description": "invoice 12"}),
            organization_id=team.org,
            user_id=team.a.id,
        )
        send = _tool("GMAIL_SEND_EMAIL", "gmail", "send-1")
        fn = connected_tools.function_name(send)
        with (
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[send])
            ),
            patch.object(db_client, "get_tool_by_uuid", AsyncMock(return_value=send)),
        ):
            first = await helper_tools.run(
                "follow_up_commitment",
                organization_id=team.org,
                arguments={
                    "commitment_id": row.id,
                    "tool": fn,
                    "arguments": {"to": "ravi@x.in", "body": "A gentle reminder"},
                },
                author_id=team.a.id,
                thread_id=None,
                helper="follow_up",
                request="remind Ravi",
            )
            again = await helper_tools.run(
                "follow_up_commitment",
                organization_id=team.org,
                arguments={"commitment_id": row.id, "tool": fn, "arguments": {}},
                author_id=team.a.id,
                thread_id=None,
                helper="follow_up",
                request="remind Ravi",
            )
        assert first["status"] == "proposed"
        assert again["status"] == "already_proposed"
        card = await db_client.get_agent_event(
            first["card_id"], organization_id=team.org
        )
        assert card.payload["args"]["commitment_id"] == row.id
        fresh = await commitments.get_visible(
            organization_id=team.org, user_id=team.a.id, commitment_id=row.id
        )
        assert (await commitments.follow_up_state(fresh))[
            "delivery"
        ] == "awaiting_approval"
        # Declining before it runs stops it: nothing is sent.
        await actions.settle(
            organization_id=team.org,
            event_id=card.id,
            verb="decline",
            user_id=team.a.id,
        )
        assert (await commitments.follow_up_state(fresh))["delivery"] == "cancelled"

    async def test_another_persons_commitment_cannot_be_followed_up(
        self, team, helpers_on
    ):
        from api.services.helpers import commitments

        row = await commitments.create(
            commitments.clean({"counterparty": "Ravi", "description": "x"}),
            organization_id=team.org,
            user_id=team.a.id,
        )
        result = await helper_tools.run(
            "follow_up_commitment",
            organization_id=team.org,
            arguments={"commitment_id": row.id, "tool": "app_x", "arguments": {}},
            author_id=team.b.id,
            thread_id=None,
            helper="follow_up",
        )
        assert result["status"] == "not_proposed"


# ---------------------------------------------------------------------------
# The builder: trackers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestBuilder:
    async def test_a_tracker_is_a_card_then_takes_rows(self, team, helpers_on):
        from api.services import acting

        with acting.acting_as(team.a.id):
            proposed = await helper_tools.run(
                "create_tracker",
                organization_id=team.org,
                arguments={
                    "name": "Client visits",
                    "columns": [{"name": "Date", "type": "date"}, "Client", "Agreed"],
                },
                author_id=team.a.id,
                thread_id=None,
                helper="builder",
            )
        card = await db_client.get_agent_event(
            proposed["card_id"], organization_id=team.org
        )
        assert (
            card.payload["label"]
            == "Create tracker Client visits (Date, Client, Agreed)"
        )
        async with _client(_as(team.a, team.org)) as client:
            assert (await client.get("/api/v1/helpers/trackers")).json() == []
        await _confirm_and_run(team.org, card.id, team.a.id)
        added = await helper_tools.run(
            "add_to_tracker",
            organization_id=team.org,
            arguments={
                "tracker": "client visits",
                "values": {"client": "Acme", "date": "2026-10-07"},
            },
            author_id=team.a.id,
            thread_id=None,
            helper="builder",
        )
        assert added["status"] == "success"
        wrong = await helper_tools.run(
            "add_to_tracker",
            organization_id=team.org,
            arguments={"tracker": "client visits", "values": {"mood": "good"}},
            author_id=team.a.id,
            thread_id=None,
            helper="builder",
        )
        assert wrong["status"] == "not_proposed" and "no column mood" in wrong["reason"]
        async with _client(_as(team.a, team.org)) as client:
            listed = (await client.get("/api/v1/helpers/trackers")).json()
            detail = (
                await client.get(f"/api/v1/helpers/trackers/{listed[0]['uuid']}")
            ).json()
        assert detail["rows"][0]["values"]["Client"] == "Acme"
        async with _client(_as(team.b, team.org)) as client:
            assert (await client.get("/api/v1/helpers/trackers")).json() == []

    async def test_nobody_signed_in_means_nothing_is_kept(self, team, helpers_on):
        result = await helper_tools.run(
            "who_owes_me",
            organization_id=team.org,
            arguments={},
            author_id=None,
            thread_id=None,
            helper=None,
        )
        assert result["status"] == "unavailable"


class TestBackground:
    def test_a_handed_off_turn_keeps_its_helper(self):
        from api.services.workflow import decibyl_tasks

        with turn.running_as("research"):
            assert decibyl_tasks._current_helper() == "research"
        assert decibyl_tasks._current_helper() is None
