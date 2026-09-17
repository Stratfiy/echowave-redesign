"""A bot built from a brief that names an app gets that app's read tools.

The founder asked for a bot that summarises his email every morning. It was
built, given a routine, and the routine fired. The bot said:

    I can't access your Gmail from here. Do you want me to connect an email
    integration or would you paste the emails for me to summarize?

Gmail was connected, with twelve tools on the account including
GMAIL_FETCH_EMAILS. The bot had none of them: every node it was built with
carried no ``tool_uuids`` at all. Five bots on that account had tools
attached by hand in the builder; every generated one had zero.

So the generator wrote a bot whose whole job was reading Gmail and did not
give it Gmail. Nothing failed, nothing warned -- it ran on schedule and
asked the person to paste their inbox in.

**Reads and writes.** The first cut attached reads only, on the grounds that
a routine runs unsupervised. That was true of routines and wrong about
everything else: a bot booking an appointment while the customer is on the
phone is supervised by the person who just asked, and a bot that can only
read is half of what anybody builds one for. So both are attached here, and
whether a write may actually run is decided per run -- see
``test_writes_wait_for_a_person``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow import brief_apps


def _tool(slug: str, *, toolkit: str, uuid: str | None = None):
    return SimpleNamespace(
        tool_uuid=uuid or f"u-{slug.lower()}",
        name=slug.replace("_", " ").title(),
        description="x",
        category="composio",
        definition={
            "type": "composio",
            "config": {"tool_slug": slug, "toolkit": toolkit},
        },
    )


GMAIL = [
    _tool("GMAIL_FETCH_EMAILS", toolkit="gmail"),
    _tool("GMAIL_GET_CONTACTS", toolkit="gmail"),
    _tool("GMAIL_SEND_EMAIL", toolkit="gmail"),
    _tool("GMAIL_CREATE_EMAIL_DRAFT", toolkit="gmail"),
]
CALENDLY = [
    _tool("CALENDLY_GET_EVENT", toolkit="calendly"),
    _tool("CALENDLY_CREATE_SCHEDULING_LINK", toolkit="calendly"),
]


class TestWhichAppsABriefNames:
    def test_the_app_said_by_name_is_found(self):
        assert brief_apps.named("Read Gmail every morning", GMAIL) == {"gmail"}

    def test_it_is_case_and_position_insensitive(self):
        assert brief_apps.named("summarise GMAIL at 8am", GMAIL) == {"gmail"}

    def test_an_app_that_is_not_connected_is_not_invented(self):
        """Naming Salesforce in a brief does not conjure a Salesforce tool."""
        assert brief_apps.named("push it to Salesforce", GMAIL) == set()

    def test_several_apps_in_one_brief(self):
        both = brief_apps.named("read Gmail, then book in Calendly", GMAIL + CALENDLY)
        assert both == {"gmail", "calendly"}

    def test_a_brief_naming_nothing_attaches_nothing(self):
        """The old behaviour, kept exactly: a bot with no app in its brief is
        built as it always was."""
        assert brief_apps.named("Answer the phone politely", GMAIL) == set()

    def test_a_word_inside_another_word_is_not_a_match(self):
        """``gmail`` must not be found inside an address or a longer token --
        that is how a bot gets a tool nobody asked for."""
        assert brief_apps.named("write to notgmailish@example.com", GMAIL) == set()


class TestWhichToolsGetAttached:
    def test_the_reads_of_a_named_app_are_attached(self):
        got = brief_apps.read_tool_uuids("Read Gmail every morning", GMAIL)
        assert set(got) == {"u-gmail_fetch_emails", "u-gmail_get_contacts"}

    def test_read_tool_uuids_is_reads_only(self):
        """It is the reads half by name, and stays that way: ``tool_uuids``
        is what callers wanting both should use."""
        got = brief_apps.read_tool_uuids("Read Gmail and reply to everyone", GMAIL)
        assert "u-gmail_send_email" not in got
        assert "u-gmail_create_email_draft" not in got

    def test_the_writes_of_a_named_app_are_attached_too(self):
        got = brief_apps.write_tool_uuids("Read Gmail and reply to everyone", GMAIL)
        assert set(got) == {"u-gmail_send_email", "u-gmail_create_email_draft"}

    def test_together_they_are_reads_first(self):
        """Under the cap a bot keeps the ability to look things up, which
        every brief needs, over the ability to change them, which only some
        do."""
        got = brief_apps.tool_uuids("Read Gmail", GMAIL)
        assert got[:2] == ["u-gmail_fetch_emails", "u-gmail_get_contacts"]
        assert set(got) == {t.tool_uuid for t in GMAIL}

    def test_the_pair_shares_one_cap(self):
        many = [
            _tool(f"GMAIL_GET_THING_{n}", toolkit="gmail", uuid=f"u-r{n}")
            for n in range(6)
        ] + [
            _tool(f"GMAIL_SEND_THING_{n}", toolkit="gmail", uuid=f"u-w{n}")
            for n in range(6)
        ]
        assert len(brief_apps.tool_uuids("Read Gmail", many)) == brief_apps.MAX_TOOLS

    def test_an_unnamed_app_contributes_nothing(self):
        got = brief_apps.read_tool_uuids("Read Gmail every morning", GMAIL + CALENDLY)
        assert not [u for u in got if "calendly" in u]

    def test_nothing_named_means_no_tools(self):
        assert brief_apps.read_tool_uuids("Answer the phone", GMAIL) == []

    def test_it_is_capped(self):
        many = [
            _tool(f"GMAIL_GET_THING_{n}", toolkit="gmail", uuid=f"u-{n}")
            for n in range(40)
        ]
        got = brief_apps.read_tool_uuids("Read Gmail", many)
        assert len(got) == brief_apps.MAX_TOOLS

    def test_the_order_is_stable(self):
        """A regenerated bot must not get a different tool list from the same
        brief and the same account."""
        first = brief_apps.read_tool_uuids("Read Gmail", GMAIL)
        second = brief_apps.read_tool_uuids("Read Gmail", list(reversed(GMAIL)))
        assert first == second


class TestPuttingThemOnTheGraph:
    def _graph(self):
        return {
            "nodes": [
                {"id": "global-1", "type": "globalNode", "data": {"name": "Persona"}},
                {"id": "start-1", "type": "startCall", "data": {"name": "Start"}},
                {"id": "agent-1", "type": "agentNode", "data": {"name": "Summarise"}},
                {"id": "end-1", "type": "endCall", "data": {"name": "End"}},
            ],
            "edges": [],
        }

    def test_they_land_on_the_nodes_that_can_call_them(self):
        out = brief_apps.attach(self._graph(), ["u-a", "u-b"])
        by_id = {n["id"]: n for n in out["nodes"]}
        assert by_id["agent-1"]["data"]["tool_uuids"] == ["u-a", "u-b"]

    def test_the_start_node_gets_them_too(self):
        """A one-node bot's work happens on the start node, and the routine
        runner's first turn is that node. Miss it and a simple bot has the
        tools nowhere it can reach them."""
        out = brief_apps.attach(self._graph(), ["u-a"])
        by_id = {n["id"]: n for n in out["nodes"]}
        assert by_id["start-1"]["data"]["tool_uuids"] == ["u-a"]

    def test_nothing_is_put_on_a_node_that_cannot_call_a_tool(self):
        out = brief_apps.attach(self._graph(), ["u-a"])
        by_id = {n["id"]: n for n in out["nodes"]}
        assert "tool_uuids" not in by_id["end-1"]["data"]
        assert "tool_uuids" not in by_id["global-1"]["data"]

    def test_an_existing_list_is_not_trampled(self):
        graph = self._graph()
        graph["nodes"][2]["data"]["tool_uuids"] = ["u-kept"]
        out = brief_apps.attach(graph, ["u-a"])
        assert out["nodes"][2]["data"]["tool_uuids"] == ["u-kept", "u-a"]

    def test_a_uuid_already_there_is_not_added_twice(self):
        graph = self._graph()
        graph["nodes"][2]["data"]["tool_uuids"] = ["u-a"]
        out = brief_apps.attach(graph, ["u-a"])
        assert out["nodes"][2]["data"]["tool_uuids"] == ["u-a"]

    def test_no_tools_leaves_the_graph_alone(self):
        graph = self._graph()
        assert brief_apps.attach(graph, []) == graph

    def test_a_graph_with_no_nodes_does_not_raise(self):
        assert brief_apps.attach({}, ["u-a"]) == {}


class TestTheBuiltBotComesOutHoldingThem:
    """The end the whole module is for: build() puts them on the graph it
    saves, so the bot that reaches the database has its tools."""

    @pytest.mark.asyncio
    async def test_build_saves_a_graph_carrying_the_tools(self):
        from api.services.workflow import bot_from_brief

        graph = {
            "nodes": [
                {"id": "start-1", "type": "startCall", "data": {"name": "Start"}},
                {"id": "agent-1", "type": "agentNode", "data": {"name": "Summarise"}},
            ],
            "edges": [],
        }
        saved: dict = {}

        async def _create_workflow(**kwargs):
            saved.update(kwargs)
            return SimpleNamespace(id=1, name=kwargs["name"], handle="@x")

        with (
            patch.object(
                bot_from_brief,
                "generate_workflow_definition",
                AsyncMock(return_value={"workflow_definition": graph}),
            ),
            patch.object(bot_from_brief, "regenerate_trigger_uuids", lambda d: d),
            patch.object(bot_from_brief, "apply_brief", lambda d, b: d),
            patch.object(bot_from_brief, "extract_trigger_paths", lambda d: []),
            patch.object(
                bot_from_brief.connected_tools,
                "list_for_organization",
                AsyncMock(return_value=GMAIL),
            ),
            patch.object(bot_from_brief.db_client, "create_workflow", _create_workflow),
            patch.object(bot_from_brief, "_schedule_it", AsyncMock(return_value=None)),
        ):
            await bot_from_brief.build(
                organization_id=7,
                user_id=3,
                args={
                    "name": "Inbox Brief",
                    "call_type": "inbound",
                    "channel": "chat",
                    "spec": "Every morning read Gmail and summarise it for me.",
                },
            )

        nodes = saved["workflow_definition"]["nodes"]
        # By node type, not node id: a brief that names a schedule is built
        # as a task graph, whose work sits on the start node rather than an
        # agent node. What matters is that the tools reached a node that can
        # call them.
        callers = [n for n in nodes if n["type"] in brief_apps.CALLING_NODES]
        assert callers, "nothing in the saved graph can call a tool"
        attached = {u for n in callers for u in (n["data"].get("tool_uuids") or [])}
        assert attached, "every calling node was saved with no tools"
        assert "u-gmail_fetch_emails" in attached
        # The write is attached as well now. What stops a routine using it is
        # the run-time gate, not its absence from the graph.
        assert "u-gmail_send_email" in attached

    @pytest.mark.asyncio
    async def test_a_tool_list_that_cannot_be_read_still_builds_the_bot(self):
        """The bot is the deliverable. Losing it over a tool lookup would be
        the worse trade, so the failure is logged and the build goes on."""
        from api.services.workflow import bot_from_brief

        with patch.object(
            bot_from_brief.connected_tools,
            "list_for_organization",
            AsyncMock(side_effect=RuntimeError("db down")),
        ):
            out = await bot_from_brief._attach_named_apps(
                {"nodes": [{"id": "a", "type": "agentNode", "data": {}}]},
                organization_id=7,
                spec="read Gmail",
            )
        assert out["nodes"][0]["data"].get("tool_uuids") is None


class TestTheCapDoesNotEatTheWrites:
    """The mistake this codebase had already made once, made again here.

    #343 shipped a tool list that ranked reads first and took the first
    twelve. Gmail publishes enough GET actions to fill twelve, so a real
    account re-synced under that rule got twelve ways to read mail and no
    way to send one -- the hole the ranking was written to close, arrived at
    from the other side. #352 fixed it by *reserving* slots rather than
    ordering: ``WRITE_SLOTS`` of the dozen are held for writes.

    ``brief_apps`` was then written with reads first and a hard cap of
    eight, and Gmail has exactly eight reads. A bot built from "read Gmail
    and reply to them" came out with eight reads and not one write -- with
    the sibling module's docstring explaining, at length, why that happens.

    So the same remedy: slots are held, and either side borrows what the
    other does not use.
    """

    GMAIL_FULL = [
        _tool(f"GMAIL_GET_{n}", toolkit="gmail", uuid=f"u-r{n}") for n in range(8)
    ] + [
        _tool("GMAIL_SEND_EMAIL", toolkit="gmail", uuid="u-send"),
        _tool("GMAIL_REPLY_TO_THREAD", toolkit="gmail", uuid="u-reply"),
        _tool("GMAIL_CREATE_EMAIL_DRAFT", toolkit="gmail", uuid="u-draft"),
        _tool("GMAIL_SEND_DRAFT", toolkit="gmail", uuid="u-senddraft"),
    ]

    def test_an_app_with_a_capful_of_reads_still_gets_writes(self):
        """The regression, in one line: eight reads must not mean no send."""
        got = brief_apps.tool_uuids("Read Gmail and reply to them", self.GMAIL_FULL)
        assert "u-send" in got

    def test_the_held_slots_are_honoured(self):
        got = brief_apps.tool_uuids("Read Gmail and reply", self.GMAIL_FULL)
        writes = [
            u for u in got if u in {"u-send", "u-reply", "u-draft", "u-senddraft"}
        ]
        assert len(writes) == brief_apps.WRITE_SLOTS

    def test_reads_still_take_what_the_writes_do_not(self):
        """An app with one write gives the rest of the cap back to the
        reads, rather than leaving the bot half-equipped."""
        one_write = [
            _tool(f"GMAIL_GET_{n}", toolkit="gmail", uuid=f"u-r{n}") for n in range(8)
        ] + [_tool("GMAIL_SEND_EMAIL", toolkit="gmail", uuid="u-send")]
        got = brief_apps.tool_uuids("Read Gmail and send", one_write)
        assert len(got) == brief_apps.MAX_TOOLS
        assert "u-send" in got
        assert len([u for u in got if u.startswith("u-r")]) == 7

    def test_writes_borrow_what_the_reads_do_not(self):
        """And the other way: an app with two reads and five writes fills
        the cap rather than stopping at the reserved four."""
        lopsided = [
            _tool(f"GMAIL_GET_{n}", toolkit="gmail", uuid=f"u-r{n}") for n in range(2)
        ] + [
            _tool(f"GMAIL_SEND_{n}", toolkit="gmail", uuid=f"u-w{n}") for n in range(5)
        ]
        got = brief_apps.tool_uuids("Read Gmail and send", lopsided)
        assert len(got) == 7

    def test_the_cap_still_holds(self):
        got = brief_apps.tool_uuids("Read Gmail and reply", self.GMAIL_FULL)
        assert len(got) == brief_apps.MAX_TOOLS

    def test_the_send_beats_the_draft(self):
        """Alphabetical is not an order. Sorted by slug alone Gmail's
        CREATE_EMAIL_DRAFT comes before SEND_EMAIL, so a bot told to reply
        got three ways to write a draft and no way to send one -- #344's
        bug, one module over."""
        got = brief_apps.write_tool_uuids("Read Gmail and reply", self.GMAIL_FULL)
        # Sends and the reply come before the draft. Which of the two sends
        # leads is not something the verb list decides, and the test does not
        # pretend it does.
        assert got.index("u-send") < got.index("u-draft")
        assert got.index("u-reply") < got.index("u-draft")
        # Under the held slots the draft used to be the one that fell off.
        # It is now held deliberately: on a schedule, sending is gated and
        # drafting is the only write a bot may use, so a bot with no draft
        # tool is a bot with no usable write. The ranking above is unchanged
        # -- the swap happens at the cap and costs the lowest-ranked write.
        kept = brief_apps.tool_uuids("Read Gmail and reply", self.GMAIL_FULL)
        assert "u-send" in kept
        assert "u-draft" in kept

    def test_an_unlisted_verb_is_last_not_lost(self):
        """An app whose writes are all unusual words still gives the bot
        something."""
        odd = [
            _tool("NOTION_APPEND_BLOCK", toolkit="gmail", uuid="u-odd"),
            _tool("GMAIL_SEND_EMAIL", toolkit="gmail", uuid="u-send"),
        ]
        got = brief_apps.write_tool_uuids("Gmail", odd)
        assert got == ["u-send", "u-odd"]
