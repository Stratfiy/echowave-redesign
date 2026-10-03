"""OP-4: the Prospecting agent, and the two mechanisms it rests on.

A bot set to approve its sends turns every connected-app write into a
card on its thread, with the sources the run read, on a call, in a chat
or on its schedule; nothing leaves until a person confirms, and the
outcome is stamped on the prospect. An agent can save what it found as
prospects. The Outbound Prospecting template hires with the web, the
mailbox and send approval, and says the things the design requires.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.enums import ToolCategory
from api.services.agent_templates import equip, get_template
from api.services.workflow import actions, prospects, send_approval, unattended
from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager

CTM = "api.services.workflow.pipecat_engine_custom_tools"


def _gmail_send(uuid="gm-1"):
    return SimpleNamespace(
        tool_uuid=uuid,
        name="Send email",
        description="",
        category=ToolCategory.COMPOSIO.value,
        definition={"config": {"toolkit": "gmail", "tool_slug": "GMAIL_SEND_EMAIL"}},
    )


def _manager(*, approve: bool, unattended_run: bool = False):
    engine = SimpleNamespace(
        _is_voice=False,
        _workflow_run_id=7,
        llm=SimpleNamespace(register_function=None),
        _get_organization_id=AsyncMock(return_value=1),
    )
    registered: dict = {}
    engine.llm.register_function = lambda name, handler, **kw: registered.__setitem__(
        name, handler
    )
    manager = CustomToolManager(engine)
    manager._attribution = {
        "organization_id": 1,
        "workflow_id": 42,
        "workflow_run_id": 7,
        "workflow_version_id": None,
    }
    manager._approval_wanted = approve
    manager._writes_gated = unattended_run and not approve
    return manager, registered


class TestASendBecomesACard:
    async def test_a_write_is_proposed_not_run_and_carries_the_sources(self):
        manager, _ = _manager(approve=True)
        manager._sources = ["https://sunrise.example/contact"]
        tool = _gmail_send()
        handler = manager._create_composio_handler(tool, "GMAIL_SEND_EMAIL")
        results = []

        async def cb(result, *a, **k):
            results.append(result)

        params = SimpleNamespace(
            arguments={"recipient_email": "priya@sunrise.example", "subject": "Hi"},
            tool_call_id="c1",
            result_callback=cb,
        )
        propose = AsyncMock(return_value={"status": "proposed", "note": "n"})
        execute = AsyncMock()
        with (
            patch("api.services.workflow.actions.propose", propose),
            patch(f"{CTM}.execute_composio_tool", execute),
        ):
            await handler(params)
        execute.assert_not_awaited()
        assert results[0]["status"] == "proposed"
        kw = propose.await_args.kwargs
        assert kw["workflow_id"] == 42
        assert kw["workflow_run_id"] == 7
        assert kw["in_channel"] is True
        args = kw["arguments"]
        assert args["action"] == actions.RUN_TOOL
        assert args["tool_uuid"] == "gm-1"
        assert args["arguments"]["recipient_email"] == "priya@sunrise.example"
        assert "https://sunrise.example/contact" in args["why"]

    async def test_a_read_still_runs(self):
        manager, _ = _manager(approve=True)
        tool = SimpleNamespace(
            tool_uuid="gm-2",
            name="Fetch emails",
            description="",
            category=ToolCategory.COMPOSIO.value,
            definition={
                "config": {"toolkit": "gmail", "tool_slug": "GMAIL_FETCH_EMAILS"}
            },
        )
        handler = manager._create_composio_handler(tool, "GMAIL_FETCH_EMAILS")
        results = []

        async def cb(result, *a, **k):
            results.append(result)

        propose = AsyncMock()
        with (
            patch("api.services.workflow.actions.propose", propose),
            patch(
                f"{CTM}.execute_composio_tool",
                AsyncMock(return_value={"status": "success", "data": []}),
            ),
            patch.object(manager, "_charge_tool_call", AsyncMock()),
            patch(
                f"{CTM}.spill.spill_if_large", AsyncMock(side_effect=lambda r, **k: r)
            ),
            patch(
                "api.services.sandbox.code_mode.allowed", AsyncMock(return_value=False)
            ),
        ):
            await handler(
                SimpleNamespace(arguments={}, tool_call_id="c", result_callback=cb)
            )
        propose.assert_not_awaited()
        assert results[0]["status"] == "success"

    async def test_on_a_routine_the_writes_stay_offered_because_they_are_cards(self):
        manager, _ = _manager(approve=True, unattended_run=True)
        manager._writes_gated = True
        tools = [_gmail_send()]
        assert await manager._minus_ungated_writes(tools) == tools

    async def test_without_approval_a_routine_still_withholds_them(self):
        manager, _ = _manager(approve=False, unattended_run=True)
        manager._writes_gated = True
        assert await manager._minus_ungated_writes([_gmail_send()]) == []

    async def test_the_sources_are_what_the_run_read(self):
        manager, registered = _manager(approve=True)
        row = SimpleNamespace(
            tool_uuid="web-1",
            name="Web",
            description="",
            category="web",
            definition={"type": "web"},
        )
        manager._load_tools = AsyncMock(return_value=[row])
        with patch("api.constants.DECIBYL_TOOLS_2026_09_ENABLED", True):
            await manager.register_handlers(["web-1"])
        results = []

        async def cb(result, *a, **k):
            results.append(result)

        with (
            patch(
                "api.services.workflow.web_tools.fetch",
                AsyncMock(
                    return_value={"status": "success", "url": "https://a.example/x"}
                ),
            ),
            patch(
                "api.services.workflow.web_tools.search",
                AsyncMock(
                    return_value={
                        "status": "success",
                        "results": [{"link": "https://b.example/"}],
                    }
                ),
            ),
            patch(f"{CTM}.app_interactions._safe_record", AsyncMock()),
        ):
            await registered["web_fetch"](
                SimpleNamespace(
                    arguments={"url": "u"}, tool_call_id="1", result_callback=cb
                )
            )
            await registered["web_search"](
                SimpleNamespace(
                    arguments={"query": "q"}, tool_call_id="2", result_callback=cb
                )
            )
        assert manager._sources == ["https://a.example/x", "https://b.example/"]
        assert "https://a.example/x, https://b.example/" in send_approval.why_line(
            manager._sources
        )

    def test_the_why_names_up_to_five_sources_and_counts_the_rest(self):
        line = send_approval.why_line([f"https://s{i}.example/" for i in range(7)])
        assert "https://s4.example/" in line
        assert "https://s5.example/" not in line
        assert "and 2 more" in line
        assert "read no outside source" in send_approval.why_line([])

    def test_only_a_plain_true_is_approval(self):
        assert send_approval.wants_approval({"approve_sends": True})
        assert not send_approval.wants_approval({"approve_sends": "true"})
        assert not send_approval.wants_approval(None)

    def test_the_routine_briefing_says_sends_are_cards(self):
        text = unattended.briefing(
            "find leads", writes_allowed=False, sends_are_cards=True
        )
        assert "becomes a card" in text
        assert "cannot send anything" not in text
        plain = unattended.briefing("find leads", writes_allowed=False)
        assert "cannot send anything" in plain


class TestTheOutcomeIsRecorded:
    def test_the_recipient_is_read_from_any_mail_tools_arguments(self):
        assert (
            send_approval.recipient_of({"recipient_email": "A@x.example"})
            == "a@x.example"
        )
        assert send_approval.recipient_of({"to": ["b@x.example"]}) == "b@x.example"
        assert (
            send_approval.recipient_of({"to_recipients": [{"email": "c@x.example"}]})
            == "c@x.example"
        )
        assert (
            send_approval.recipient_of({"to": "Priya <p@x.example>"}) == "p@x.example"
        )
        assert send_approval.recipient_of({"body": "x"}) is None

    async def test_a_sent_mail_stamps_the_prospect(self):
        row = SimpleNamespace(id=9, email_normalized="priya@sunrise.example")
        db = SimpleNamespace(
            search_contacts_for_organization=AsyncMock(return_value=[row]),
            touch_contact=AsyncMock(return_value=True),
        )
        with patch("api.db.db_client", db):
            ok = await send_approval.note_sent(
                1, {"recipient_email": "Priya@Sunrise.example", "subject": "Hello"}
            )
        assert ok
        kw = db.touch_contact.call_args.kwargs
        assert db.touch_contact.call_args.args[0] == 9
        assert kw["organization_id"] == 1
        assert kw["attributes"]["last_subject"] == "Hello"
        assert kw["attributes"]["status"] == "emailed"
        assert kw["attributes"]["last_emailed_at"]

    async def test_a_declined_card_stamps_the_prospect_too(self):
        row = SimpleNamespace(id=9, email_normalized="priya@sunrise.example")
        db = SimpleNamespace(
            search_contacts_for_organization=AsyncMock(return_value=[row]),
            touch_contact=AsyncMock(return_value=True),
        )
        with patch("api.db.db_client", db):
            await send_approval.note_declined(1, {"to": "priya@sunrise.example"})
        assert db.touch_contact.call_args.kwargs["attributes"]["status"] == "declined"

    async def test_an_unknown_recipient_is_no_stamp_and_no_error(self):
        db = SimpleNamespace(
            search_contacts_for_organization=AsyncMock(return_value=[]),
            touch_contact=AsyncMock(),
        )
        with patch("api.db.db_client", db):
            assert not await send_approval.note_sent(1, {"to": "x@y.example"})
        db.touch_contact.assert_not_awaited()

    async def test_executing_a_confirmed_send_records_the_outcome(self):
        tool = _gmail_send()
        payload = {
            "action": actions.RUN_TOOL,
            "args": {
                "tool_uuid": "gm-1",
                "tool_name": "Send email",
                "arguments": {"recipient_email": "p@x.example", "subject": "s"},
            },
            "confirmed": {"at": "2026-09-21T10:00:00+00:00"},
        }
        note = AsyncMock(return_value=True)
        with (
            patch(
                "api.services.workflow.actions.db_client.get_tool_by_uuid",
                AsyncMock(return_value=tool),
            ),
            patch("api.services.workflow.connected_tools.is_connected", lambda t: True),
            patch(
                "api.services.workflow.connected_tools.execute",
                AsyncMock(return_value={"status": "success", "data": {"id": "m1"}}),
            ),
            patch("api.services.workflow.send_approval.note_sent", note),
        ):
            line = await actions._execute(1, payload)
        assert line.startswith("Done")
        note.assert_awaited_once_with(
            1, {"recipient_email": "p@x.example", "subject": "s"}
        )


class TestSavingProspects:
    def test_rows_need_an_address_or_a_number(self):
        rows, skipped = prospects.rows_from(
            {
                "country": "in",
                "prospects": [
                    {
                        "name": "Sunrise",
                        "email": "hello@sunrise.example",
                        "source_url": "https://sunrise.example/",
                        "note": "fits",
                    },
                    {"name": "Nobody"},
                    {"name": "Phone only", "phone": "044 2345 6789", "company": "Acme"},
                    "junk",
                ],
            }
        )
        assert skipped == 2
        assert rows[0]["email"] == "hello@sunrise.example"
        assert rows[0]["phone_normalized"] is None
        assert rows[0]["attributes"]["source_url"] == "https://sunrise.example/"
        assert rows[0]["attributes"]["found_at"]
        assert rows[1]["phone_normalized"] == "+914423456789"
        assert rows[1]["attributes"]["company"] == "Acme"

    async def test_saves_into_a_prospects_list_made_once(self):
        db = SimpleNamespace(
            get_contact_lists=AsyncMock(return_value=[]),
            create_contact_list=AsyncMock(return_value=SimpleNamespace(id=5)),
            upsert_contacts=AsyncMock(return_value=(1, 0)),
        )
        with patch("api.db.db_client", db):
            out = await prospects.save(
                1, {"prospects": [{"email": "a@x.example"}, {"name": "no way"}]}
            )
        assert out == {
            "status": "success",
            "list": "Prospects",
            "saved": 1,
            "skipped": 1,
        }
        assert db.create_contact_list.call_args.kwargs["name"] == "Prospects"
        assert db.upsert_contacts.call_args.args[0] == 5

        db.get_contact_lists = AsyncMock(
            return_value=[SimpleNamespace(id=8, name="prospects")]
        )
        db.create_contact_list.reset_mock()
        with patch("api.db.db_client", db):
            await prospects.save(1, {"prospects": [{"email": "b@x.example"}]})
        db.create_contact_list.assert_not_called()
        assert db.upsert_contacts.call_args.args[0] == 8

    async def test_nothing_usable_is_an_error_the_model_can_act_on(self):
        out = await prospects.save(1, {"prospects": [{"name": "x"}]})
        assert out["status"] == "error"
        assert out["skipped"] == 1

    def test_decibyl_and_an_agent_both_have_it(self):
        from api.services.workflow import agent_web, decibyl

        with patch("api.constants.DECIBYL_TOOLS_2026_09_ENABLED", True):
            assert prospects.TOOL_NAME in [t["name"] for t in decibyl.office_tools()]
            assert prospects.TOOL_NAME in decibyl.SYSTEM
        text = [f["function"]["name"] for f in agent_web.function_schemas(voice=False)]
        voice = [f["function"]["name"] for f in agent_web.function_schemas(voice=True)]
        assert prospects.TOOL_NAME in text
        assert prospects.TOOL_NAME not in voice


class TestTheProspectingTemplate:
    def test_it_is_in_the_catalogue_with_what_it_needs(self):
        t = get_template("outbound_prospecting")
        assert t is not None
        assert t.needs_web and t.approve_sends
        assert "gmail" in t.apps and "outlook" in t.apps
        assert t.function == "Follow up leads"

    def test_it_says_the_things_the_design_requires(self):
        t = get_template("outbound_prospecting")
        joined = " ".join(n.prompt for n in t.nodes).lower()
        rails = " ".join(t.guardrails).lower()
        assert "linkedin" in rails and "never" in rails
        assert "save_prospects" in joined
        assert "search_records" in joined
        assert "no more" in joined  # a way to say no, in every draft
        assert "card" in joined
        assert "never send an email yourself" in rails
        assert any("can-spam" in n.lower() for n in t.compliance_notes)

    async def test_hiring_it_gives_web_mailbox_and_send_approval(self):
        t = get_template("outbound_prospecting")
        definition = {
            "nodes": [
                {"id": "start-1", "type": "startCall", "data": {}},
                {"id": "agent-1", "type": "agentNode", "data": {}},
                {"id": "end-1", "type": "endCall", "data": {}},
            ],
            "edges": [],
        }
        gmail = _gmail_send("gm-1")
        with (
            patch(
                "api.services.workflow.agent_web.ensure_tool",
                AsyncMock(return_value="web-1"),
            ),
            patch(
                "api.services.workflow.connected_tools.list_for_organization",
                AsyncMock(return_value=[gmail]),
            ),
            patch(
                "api.services.workflow.connected_tools.mcp_for_organization",
                AsyncMock(return_value=[]),
            ),
        ):
            out = await equip.for_template(
                definition, template=t, organization_id=1, user_id=5
            )
        by_id = {n["id"]: n for n in out["nodes"]}
        assert by_id["agent-1"]["data"]["tool_uuids"] == ["web-1", "gm-1"]
        assert "tool_uuids" not in by_id["end-1"]["data"]
        # The hire also says which channel the bot is on (a scheduled
        # template writes, so chat); that is its own story and test.
        assert equip.configurations(None, template=t) == {
            "approve_sends": True,
            "channel": "chat",
        }
        assert equip.configurations({"x": 1}, template=t) == {
            "x": 1,
            "approve_sends": True,
            "channel": "chat",
        }

    async def test_a_template_that_needs_nothing_gets_nothing(self):
        t = get_template("compliance_reminder")
        definition = {
            "nodes": [{"id": "a", "type": "agentNode", "data": {}}],
            "edges": [],
        }
        ensure = AsyncMock()
        with patch("api.services.workflow.agent_web.ensure_tool", ensure):
            out = await equip.for_template(
                definition, template=t, organization_id=1, user_id=5
            )
        ensure.assert_not_awaited()
        assert out["nodes"][0]["data"] == {}
        assert equip.configurations({"x": 1}, template=t) == {
            "x": 1,
            "channel": "chat",
        }

    async def test_a_web_tool_that_cannot_be_made_costs_the_hire_nothing(self):
        t = get_template("outbound_prospecting")
        definition = {
            "nodes": [{"id": "a", "type": "agentNode", "data": {}}],
            "edges": [],
        }
        with (
            patch(
                "api.services.workflow.agent_web.ensure_tool",
                AsyncMock(side_effect=RuntimeError("db away")),
            ),
            patch(
                "api.services.workflow.connected_tools.list_for_organization",
                AsyncMock(return_value=[]),
            ),
            patch(
                "api.services.workflow.connected_tools.mcp_for_organization",
                AsyncMock(return_value=[]),
            ),
        ):
            out = await equip.for_template(
                definition, template=t, organization_id=1, user_id=5
            )
        assert out["nodes"][0]["data"] == {}


class TestTheOutreachUpgrade:
    """Replies first, one follow-up, junk addresses refused, and nothing a
    send recorded is lost when a prospect is found again."""

    def test_junk_addresses_are_refused_with_the_reason(self):
        assert prospects.junk_reason("logo@2x.png") is not None
        assert "tracking" in prospects.junk_reason("abc123@sentry.wixpress.com")
        assert "placeholder" in prospects.junk_reason("you@example.com")
        assert "no-reply" in prospects.junk_reason("noreply@clinic.example")
        assert prospects.junk_reason("not an address@") is not None
        assert prospects.junk_reason("dr.priya@sunrise-dental.in") is None
        assert prospects.junk_reason("info@x.example") is None

    def test_a_junk_address_is_said_back_to_the_model(self):
        rows, rejected = prospects.rows_and_rejects(
            {"prospects": [{"name": "Wix site", "email": "x@sentry.wixpress.com"}]}
        )
        assert rows == []
        assert rejected[0]["who"] == "Wix site"
        assert "tracking" in rejected[0]["why"]

    def test_fit_hook_and_a_reply_are_recorded(self):
        rows, _ = prospects.rows_from(
            {
                "prospects": [
                    {
                        "email": "a@x.example",
                        "fit_score": 9,
                        "hook": "Opened a second branch in Adyar",
                        "status": "interested",
                        "reply_note": "Asked about pricing",
                    },
                    {"email": "b@x.example", "status": "emailed", "fit_score": "x"},
                ]
            }
        )
        first, second = (r["attributes"] for r in rows)
        assert first["fit_score"] == 5
        assert first["hook"].startswith("Opened")
        assert first["status"] == "interested" and first["status_at"]
        assert first["reply_note"] == "Asked about pricing"
        # The model cannot claim a send happened, and a bad score is dropped.
        assert "status" not in second and "fit_score" not in second

    async def test_saving_merges_rather_than_replaces(self):
        db = SimpleNamespace(
            get_contact_lists=AsyncMock(
                return_value=[SimpleNamespace(id=8, name="Prospects")]
            ),
            upsert_contacts=AsyncMock(return_value=(1, 0)),
        )
        with patch("api.db.db_client", db):
            out = await prospects.save(
                1,
                {
                    "prospects": [
                        {"email": "a@x.example"},
                        {"name": "Logo", "email": "logo@2x.png"},
                    ]
                },
            )
        assert db.upsert_contacts.call_args.kwargs["merge_attributes"] is True
        assert out["saved"] == 1 and out["skipped"] == 1
        assert out["rejected"][0]["who"] == "Logo"

    def test_the_merge_keeps_what_a_send_recorded_and_a_stop(self):
        from api.db import contact_client

        sql = str(contact_client._MERGED_ATTRIBUTES)
        assert "contacts.attributes::jsonb || excluded.attributes::jsonb" in sql
        assert "'found_at'" in sql
        for status in ("declined", "unsubscribed", "bounced", "not_interested"):
            assert status in contact_client.STOP_STATUSES
            assert f"'{status}'" in sql

    async def _sent(self, existing):
        row = SimpleNamespace(id=9, email_normalized="p@x.example", attributes=existing)
        db = SimpleNamespace(
            search_contacts_for_organization=AsyncMock(return_value=[row]),
            touch_contact=AsyncMock(return_value=True),
        )
        with patch("api.db.db_client", db):
            await send_approval.note_sent(1, {"to": "p@x.example", "subject": "Re: hi"})
        return db.touch_contact.call_args.kwargs["attributes"]

    async def test_a_send_counts_the_emails(self):
        first = await self._sent({})
        assert first["emails_sent"] == 1 and first["status"] == "emailed"
        assert first["first_emailed_at"] == first["last_emailed_at"]
        second = await self._sent(
            {"emails_sent": 1, "first_emailed_at": "2026-09-01T09:00:00+00:00"}
        )
        assert second["emails_sent"] == 2
        assert second["first_emailed_at"] == "2026-09-01T09:00:00+00:00"

    async def test_answering_a_reply_keeps_the_conversation_status(self):
        out = await self._sent({"status": "interested", "emails_sent": 1})
        assert "status" not in out
        assert out["emails_sent"] == 2

    def test_the_template_runs_replies_then_follow_ups_then_new(self):
        t = get_template("outbound_prospecting")
        names = [n.name for n in t.nodes]
        assert names[0] == "Check replies" and t.nodes[0].type == "startCall"
        assert names.index("Follow up") < names.index("Find prospects")
        assert names.index("Find prospects") < names.index("Draft first emails")
        # Every edge names a step that exists.
        for edge in t.edges:
            assert edge.source in names and edge.target in names
        rails = " ".join(t.guardrails).lower()
        assert "never a third" in rails
        assert "unsubscribed" in rails
        joined = " ".join(n.prompt for n in t.nodes)
        assert "{{booking_link}}" in joined and "{{follow_up_days}}" in joined
        # An optional fact left blank must not reach a prospect as braces.
        assert joined.count("still in double braces") >= 3
