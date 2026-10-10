"""Four ways to send a model fewer tokens, each off until it is measured.

``cache_v2`` (more of a request is read from the cache), ``lean_tools`` (a
quick turn is offered fewer tools), ``cheap_routing`` (the small model for
work that is not a conversation, and Auto's word rules tightened) and
``history_cap`` (the older thread as a digest, a file's opening).

One class per lever, then the test the levers owe each other: with every one
on, a person who asked for a tool still gets it, and the thread's ids and
cards still reach the model. A token cut that makes the assistant unable to do
what it was asked has saved nothing.
"""

from __future__ import annotations

import json
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api import constants
from api.services import features
from api.services.agent_builder import client, settings
from api.services.agent_builder.client import ModelReply, ToolCall
from api.services.routing import brain, tool_intents
from api.services.workflow import (
    connected_tools,
    decibyl,
    history_cap,
    lean_tools,
)

ORG = 7
LEVERS = ("cache_v2", "lean_tools", "cheap_routing", "history_cap")


@pytest.fixture
def only(monkeypatch):
    """Switch exactly the named flags on, everything else off."""

    def switch(*names: str) -> None:
        monkeypatch.setattr(
            features, "is_on", lambda name, organization_id=None: name in names
        )

    return switch


def _tool(name: str) -> dict:
    return {
        "name": name,
        "description": f"{name} tool",
        "parameters": {"type": "object", "properties": {}},
    }


# The whole drawer a fully switched-on account has, as plain names. Not
# derived from the code: a tool that is renamed or added must make a test here
# say so (see ``TestEveryToolIsAccountedFor``).
EVERY_TOOL = sorted(
    set(lean_tools.CORE)
    | {name for _, tools in tool_intents.INTENTS.values() for name in tools}
)


# --- cache_v2 -----------------------------------------------------------------


class TestCacheV2:
    """A breakpoint after the tools, one on the thread so far, one on the end
    of the turn, and the per-person words after the shared system prompt."""

    def _conversation(self, prior: int = 4) -> client.Conversation:
        conversation = client.Conversation()
        for i in range(prior):
            if i % 2 == 0:
                conversation.add_user(f"question {i}")
            else:
                conversation.messages.append(
                    {"role": "assistant", "content": f"answer {i}"}
                )
        conversation.stable_prefix = prior
        conversation.add_user("context\n\n## Question\nnow")
        return conversation

    def _marked(self, payload: dict) -> int:
        return json.dumps(payload).count('"cache_control"')

    def test_off_the_request_is_the_one_it_always_was(self, only):
        only()
        tools = [_tool("a"), _tool("b")]
        system = client.SplitSystem("shared ", "personal")
        payload = client._anthropic_request(
            model="m", system=system, conversation=self._conversation(), tools=tools
        )
        assert [b["text"] for b in payload["system"]] == ["shared personal"]
        assert self._marked(payload) == 1, "the system block only"
        assert "cache_control" not in json.dumps(payload["tools"])

    def test_on_there_are_four_breakpoints_in_render_order(self, only):
        only("cache_v2")
        payload = client._anthropic_request(
            model="m",
            system=client.SplitSystem("shared ", "personal"),
            conversation=self._conversation(),
            tools=[_tool("a"), _tool("b")],
        )
        assert self._marked(payload) == 4
        # Tools: the last one carries it, so the whole list is the prefix.
        assert "cache_control" not in payload["tools"][0]
        assert payload["tools"][-1]["cache_control"] == {"type": "ephemeral"}
        # System: the shared half carries it, the per-person half does not.
        shared, personal = payload["system"]
        assert shared["text"] == "shared " and "cache_control" in shared
        assert personal["text"] == "personal" and "cache_control" not in personal
        # The thread so far: the last prior message. The turn's end: the last.
        messages = payload["messages"]
        marked = [
            i
            for i, m in enumerate(messages)
            if "cache_control" in json.dumps(m["content"])
        ]
        assert marked == [3, 4]

    def test_a_system_without_a_personal_half_is_one_block(self, only):
        only("cache_v2")
        payload = client._anthropic_request(
            model="m",
            system=client.SplitSystem("all shared", ""),
            conversation=self._conversation(),
            tools=[],
        )
        assert [b["text"] for b in payload["system"]] == ["all shared"]
        assert "cache_control" in payload["system"][0]

    def test_a_plain_system_string_still_works_on(self, only):
        only("cache_v2")
        payload = client._anthropic_request(
            model="m", system="plain", conversation=self._conversation(), tools=[]
        )
        assert payload["system"][0]["text"] == "plain"

    def test_no_prior_turns_marks_no_prefix(self, only):
        only("cache_v2")
        conversation = client.Conversation()
        conversation.add_user("only")
        payload = client._anthropic_request(
            model="m", system="s", conversation=conversation, tools=[]
        )
        assert self._marked(payload) == 2, "the system block and the tail"

    def test_the_conversation_itself_is_never_marked(self, only):
        only("cache_v2")
        conversation = self._conversation()
        before = json.dumps(conversation.messages)
        client._anthropic_request(
            model="m", system="s", conversation=conversation, tools=[_tool("a")]
        )
        assert json.dumps(conversation.messages) == before

    def test_a_split_system_reads_as_the_whole_prompt_everywhere_else(self):
        system = client.SplitSystem("shared ", "personal")
        assert system == "shared personal" and isinstance(system, str)
        assert (
            client._openai_request(
                model="m", system=system, conversation=self._conversation(), tools=[]
            )["messages"][0]["content"]
            == "shared personal"
        )

    def test_the_personal_words_move_last_and_nothing_is_lost(self, monkeypatch):
        from api.services.settings import profile

        monkeypatch.setattr(profile, "turn_block", lambda: "\n\nPERSONAL")
        whole = decibyl.system_prompt(ORG)
        shared, personal = decibyl.system_split(ORG)
        assert personal == "\n\nPERSONAL"
        assert not shared.endswith("PERSONAL") and "PERSONAL" not in shared
        assert sorted((shared + personal).split("\n")) == sorted(whole.split("\n"))
        # With no personal words the two are the same text.
        monkeypatch.setattr(profile, "turn_block", lambda: "")
        assert "".join(decibyl.system_split(ORG)) == decibyl.system_prompt(ORG)

    @pytest.mark.asyncio
    async def test_a_decibyl_turn_sends_the_split_system_only_when_on(
        self, only, monkeypatch
    ):
        from api.services.settings import profile

        monkeypatch.setattr(profile, "turn_block", lambda: "\n\nPERSONAL")
        seen = {}

        async def stream(**kwargs):
            seen["system"] = kwargs["system"]
            return ModelReply(text="ok")

        model = SimpleNamespace(provider="anthropic", model="m", api_key="k")
        with patch("api.services.agent_builder.client.stream", new=stream):
            only()
            await decibyl._speak(model, client.Conversation(), ORG, tools=[])
            assert not isinstance(seen["system"], client.SplitSystem)
            only("cache_v2")
            await decibyl._speak(model, client.Conversation(), ORG, tools=[])
        assert isinstance(seen["system"], client.SplitSystem)
        assert seen["system"].volatile.startswith("\n\nPERSONAL")


# --- lean_tools ---------------------------------------------------------------


class TestLeanTools:
    def test_a_quick_turn_gets_the_core_and_a_way_to_ask_for_the_rest(self):
        full = [_tool(n) for n in EVERY_TOOL] + [_tool("app_gmail_fetch_emails")]
        picked = lean_tools.select(full, text="thanks", used=())
        names = [t["name"] for t in picked.tools]
        assert set(lean_tools.CORE) <= set(names)
        assert "app_gmail_fetch_emails" in names, "the person's own apps stay"
        assert "make_images" not in names and "make_images" in picked.deferred
        assert names[-1] == lean_tools.MORE_TOOLS
        assert len(names) <= len(lean_tools.CORE) + 3

    def test_what_is_left_out_is_named_to_the_model(self):
        picked = lean_tools.select([_tool(n) for n in EVERY_TOOL], text="hi")
        description = picked.tools[-1]["description"]
        for name in picked.deferred:
            assert name in description

    def test_it_keeps_the_fulls_order_so_the_prefix_is_stable(self):
        full = [_tool(n) for n in EVERY_TOOL]
        names = [t["name"] for t in lean_tools.select(full, text="hi").tools]
        assert names[:-1] == [n for n in EVERY_TOOL if n in names]

    def test_a_tool_the_thread_used_stays_offered(self):
        full = [_tool(n) for n in EVERY_TOOL]
        picked = lean_tools.select(full, text="and now?", used={"make_images"})
        assert "make_images" in [t["name"] for t in picked.tools]

    def test_nothing_left_out_means_no_extra_tool(self):
        full = [_tool(n) for n in lean_tools.CORE]
        picked = lean_tools.select(full, text="hi")
        assert not picked.narrowed
        assert [t["name"] for t in picked.tools] == list(lean_tools.CORE)

    def test_used_tools_are_read_from_a_conversation_and_from_events(self):
        conversation = client.Conversation()
        conversation.add_assistant(
            ModelReply(
                text="", tool_calls=(ToolCall(id="1", name="recall", arguments={}),)
            )
        )
        assert lean_tools.used_in(conversation.messages) == {"recall"}

    def test_a_threads_used_tools_are_remembered_for_good(self):
        now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
        old = now - timedelta(days=3)
        rows = [
            SimpleNamespace(payload={"tools_used": ["recall"]}, at=now),
            SimpleNamespace(payload={"tools_used": ["make_images"]}, at=old),
            SimpleNamespace(payload={}, at=old),
            SimpleNamespace(payload=None, at=old),
        ]
        carried = lean_tools.carried_from(rows, now)
        assert carried.used == {"recall", "make_images"}

    def test_what_was_offered_counts_only_while_the_cache_is_warm(self):
        now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
        warm = SimpleNamespace(
            payload={"tools_kept": ["find_leads"]}, at=now - timedelta(seconds=60)
        )
        cold = SimpleNamespace(
            payload={"tools_kept": ["find_leads"]}, at=now - timedelta(minutes=10)
        )
        assert lean_tools.carried_from([warm], now).warm_extras == {"find_leads"}
        assert lean_tools.carried_from([cold], now).warm_extras == set()

    def test_a_warm_full_list_is_noticed_and_a_cold_one_is_not(self):
        now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
        full = {"tools_full": True}
        assert lean_tools.carried_from(
            [SimpleNamespace(payload=full, at=now - timedelta(seconds=30))], now
        ).warm_full
        assert not lean_tools.carried_from(
            [SimpleNamespace(payload=full, at=now - timedelta(minutes=9))], now
        ).warm_full

    def test_only_the_newest_reply_decides_what_is_warm(self):
        now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
        rows = [
            SimpleNamespace(payload={"tools_kept": []}, at=now - timedelta(seconds=20)),
            SimpleNamespace(
                payload={"tools_full": True}, at=now - timedelta(seconds=90)
            ),
        ]
        carried = lean_tools.carried_from(rows, now)
        assert not carried.warm_full and carried.warm_extras == set()

    def test_a_warm_extra_stays_offered_on_a_message_that_does_not_ask_for_it(self):
        full = [_tool(n) for n in EVERY_TOOL]
        picked = lean_tools.select(full, text="thanks", warm_extras={"make_images"})
        assert "make_images" in [t["name"] for t in picked.tools]
        assert "make_images" in picked.extras


class TestEveryToolIsAccountedFor:
    """Silent Absence (api/AGENTS.md): a rule that removes things, where being
    wrong produces nothing. A tool the product offers that is in neither the
    core set nor the intent table would be dropped from every quick turn and
    found by nobody. This is where that is noticed."""

    def _real_tools(self, monkeypatch) -> list[str]:
        monkeypatch.setattr(features, "is_on", lambda name, organization_id=None: True)
        monkeypatch.setattr(features, "on_anywhere", lambda name: True)
        for flag in (
            "DECIBYL_TOOLS_2026_09_ENABLED",
            "PROCUREMENT_DOCS_2026_09_ENABLED",
            "TABLE_TOOLS_ENABLED",
            "DECIBYL_BROWSER_ENABLED",
            "OUTREACH_ENABLED",
            "CALL_FOR_ME_ENABLED",
            "CALL_WHEN_DONE_ENABLED",
        ):
            monkeypatch.setattr(constants, flag, True, raising=False)
        return [t["name"] for t in decibyl.office_tools(ORG)]

    def test_each_office_tool_is_core_or_reachable_by_intent(self, monkeypatch):
        names = self._real_tools(monkeypatch)
        assert len(names) > 40, "the switched-on drawer was not built"
        reachable = set(lean_tools.CORE) | {
            n for _, tools in tool_intents.INTENTS.values() for n in tools
        }
        missing = sorted(set(names) - reachable)
        assert not missing, (
            f"{missing} are offered but neither core nor reachable by intent, so a "
            "lean turn would never offer them. Add each to lean_tools.CORE or to "
            "a tool_intents.INTENTS entry."
        )

    def test_each_name_in_the_tables_is_a_tool_that_exists(self, monkeypatch):
        names = set(self._real_tools(monkeypatch)) | {
            lean_tools.MORE_TOOLS,
            "run_script",
            "order_search",
            "order_prepare",
            "compare_prices",
            "connect_outside_tool",
        }
        named = set(lean_tools.CORE) | {
            n for _, tools in tool_intents.INTENTS.values() for n in tools
        }
        # A renamed tool must not leave a dead name behind, which would stop
        # the intent from offering the tool's replacement.
        assert not (named - names), sorted(named - names)

    def test_every_pattern_compiles_to_something_that_matches_its_own_words(self):
        for intent, (pattern, tools) in tool_intents.INTENTS.items():
            assert tools, intent
            assert pattern.pattern, intent


class TestTheRouterFlagsIntent:
    @pytest.mark.parametrize(
        "text, tool",
        [
            ("make a poster for diwali", "make_images"),
            ("can you design a logo", "make_images"),
            ("find leads for dentists in pune", "find_leads"),
            ("draft a purchase order for the cement", "draft_document"),
            ("rank these suppliers by price", "rank_table"),
            ("export that table to excel", "export_table"),
            ("match this invoice against the PO", "match_invoice"),
            ("is this message a scam?", "check_for_scam"),
            ("remind me about my medicine", "set_medicine_reminder"),
            ("book a slot for monday", "set_up_booking"),
            ("call my supplier for me", "call_for_me"),
            ("who owes me money", "who_owes_me"),
            ("run a python script over every row", "run_script"),
            ("browse the website and click the form", "browse"),
            ("install skills from this github repo", "install_from_repository"),
        ],
    )
    def test_plain_words_point_at_the_tool(self, text, tool):
        assert tool in tool_intents.flagged(text)

    def test_a_tool_named_outright_is_found(self):
        names = ["check_bot", "recall", "make_images"]
        assert tool_intents.named("use check_bot on the front desk", names) == {
            "check_bot"
        }
        assert tool_intents.named("please check bot Asha", names) == {"check_bot"}
        assert tool_intents.named("hello", names) == frozenset()

    def test_asked_for_is_restricted_to_what_exists(self):
        assert tool_intents.asked_for("make a poster", ["recall"]) == frozenset()


# --- cheap_routing ------------------------------------------------------------


class TestCheapRouting:
    @pytest.mark.parametrize(
        "text",
        [
            "send the report to Ravi",
            "what is the status of my email",
            "list my meetings for today",
            "why is the line busy?",
            "reply to Asha",
            "any update on the invoice",
        ],
    )
    def test_ordinary_words_no_longer_escalate(self, text):
        assert brain.by_rules(text, tight=True) == "quick"

    @pytest.mark.parametrize(
        "text, kind",
        [
            ("draft a reply to this complaint", "steps"),
            ("summarise the thread and plan the follow-up", "steps"),
            ("please translate this into Hindi", "steps"),
            ("hi", "quick"),
        ],
    )
    def test_strong_words_still_decide(self, text, kind):
        assert brain.by_rules(text, tight=True) == kind

    def test_a_real_analysis_is_still_deep(self):
        text = (
            "Please compare these two vendors in depth and give me the trade-offs "
            "of each, then evaluate which is the better risk for us this year."
        )
        assert brain.by_rules(text, tight=True) == "deep"

    def test_a_weak_word_counts_beside_enough_text(self):
        text = "send " + "the figures to the whole team and note the changes " * 4
        assert brain.by_rules(text, tight=True) == "steps"
        assert brain.by_rules(text) == "steps"

    def test_attachments_and_length_still_escalate(self):
        assert brain.by_rules("here", attachments=1, tight=True) == "steps"
        assert brain.by_rules("x" * 400, tight=True) == "steps"
        assert brain.by_rules("x" * 1300, tight=True) == "deep"

    def test_the_default_rules_are_the_ones_laya_is_measured_against(self):
        assert brain.by_rules("send the report to Ravi") == "steps"
        assert brain.by_rules("send the report to Ravi", tight=False) == "steps"

    @pytest.mark.asyncio
    async def test_the_flag_chooses_the_rules(self, only):
        only("cheap_routing")
        routed = await brain.route("send the report to Ravi", organization_id=ORG)
        assert routed.kind == "quick" and routed.preset == "everyday"
        only()
        routed = await brain.route("send the report to Ravi", organization_id=ORG)
        assert routed.kind == "steps"

    def test_the_cheap_model_is_asked_for_by_tier_and_only_while_on(self, only):
        only("cheap_routing")
        assert settings.cheap_choice(ORG) == "everyday"
        only()
        assert settings.cheap_choice(ORG) is None

    @pytest.mark.asyncio
    async def test_a_classifier_asks_for_the_small_tier_when_on(self, only):
        from api.services.compliance import acceptable_use

        model = SimpleNamespace(provider="anthropic", model="m", api_key="k")
        choice = AsyncMock(return_value=model)
        reply = SimpleNamespace(tool_calls=())
        for flags, expected in (((), None), (("cheap_routing",), "everyday")):
            only(*flags)
            with (
                patch.object(acceptable_use.settings, "resolve_choice", choice),
                patch.object(acceptable_use, "complete", AsyncMock(return_value=reply)),
            ):
                await acceptable_use.screen(
                    None, instructions="Book appointments.", organization_id=ORG
                )
            assert choice.await_args.args[1] == expected

    @pytest.mark.asyncio
    async def test_a_conversation_is_never_moved_to_the_small_tier(self, only):
        """Studio, the builder and Decibyl's own turns pick their model as they
        did: the cheap tier is for classifiers and extractors."""
        only("cheap_routing")
        picked = []

        async def resolve_choice(session, choice):
            picked.append(choice)
            return SimpleNamespace(provider="anthropic", model="m", api_key="k")

        with patch.object(settings, "resolve_choice", resolve_choice):
            await settings.resolve_for_organization(None, None, organization_id=ORG)
        assert picked == [None]


# --- history_cap --------------------------------------------------------------


def _thread_of(n: int) -> list[dict]:
    return [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"message {i} " + "words " * 60,
        }
        for i in range(n)
    ]


class TestHistoryCap:
    def test_a_short_thread_is_sent_whole(self):
        history = _thread_of(history_cap.KEEP)
        capped, stable = history_cap.cap(history)
        assert capped == history and stable == len(history)

    def test_the_recent_messages_go_whole_and_the_older_as_a_digest(self):
        history = _thread_of(20)
        capped, _ = history_cap.cap(history)
        keep = history_cap.recent_count(20)
        assert capped[1:] == history[-keep:]
        assert capped[0]["content"].startswith(history_cap.HEADER)
        assert f"message {20 - keep - 1} " in capped[0]["content"]
        assert len(json.dumps(capped)) < len(json.dumps(history)) * 0.65

    def test_the_digest_only_changes_when_the_boundary_moves(self):
        """The cache holds the digest and the messages after it; if it changed
        every message the cut would break the cache it is meant to feed."""
        digests = set()
        for n in range(history_cap.KEEP + 1, history_cap.KEEP + 1 + history_cap.STEP):
            capped, _ = history_cap.cap(_thread_of(n))
            digests.add(capped[0]["content"])
        # One digest across STEP consecutive lengths, bar the first step.
        assert len(digests) <= 2

    def test_a_longer_thread_appends_to_what_was_sent(self):
        a, _ = history_cap.cap(_thread_of(14))
        b, _ = history_cap.cap(_thread_of(15))
        assert b[: len(a)] == a

    def test_ids_and_cards_survive_the_digest(self):
        uuid = "3f2b8c1e-9d4a-4e6b-8a7c-1b2c3d4e5f60"
        history = _thread_of(20)
        history[2]["content"] = (
            "[Images made: poster: option 2 " + uuid + "] " + "x" * 300
        )
        history[4]["content"] = (
            "[Proposed: Turn on front desk -- proposed] " + "y" * 300
        )
        capped, _ = history_cap.cap(history)
        assert uuid in capped[0]["content"]
        assert "[Proposed: Turn on front desk" in capped[0]["content"]

    def test_the_digest_is_bounded_and_keeps_the_newest_lines(self):
        capped, _ = history_cap.cap(_thread_of(400))
        assert len(capped[0]["content"]) <= history_cap.DIGEST_CHARS + 50
        older = 400 - history_cap.recent_count(400)
        assert f"message {older - 1} " in capped[0]["content"]

    @pytest.mark.asyncio
    async def test_a_file_is_clipped_with_its_size_said_not_with_an_ellipsis(self):
        document = SimpleNamespace(full_text="x" * 50_000)
        attachment = {"document_uuid": "d", "filename": "spec.docx"}
        with patch.object(
            decibyl.db_client, "get_document_by_uuid", AsyncMock(return_value=document)
        ):
            whole = await decibyl.attached_block(ORG, [attachment])
            capped = await decibyl.attached_block(ORG, [attachment], capped=True)
        assert len(capped) < len(whole)
        assert "first 16,000 of 50,000 characters" in capped
        assert "search_files" in capped

    @pytest.mark.asyncio
    async def test_the_largest_brief_the_product_has_seen_is_sent_whole(self):
        """A vendor spec of 12,134 characters: clipping its tail drops the
        escalation and disposition steps, which was a bug once."""
        brief = "step " * 2_426
        document = SimpleNamespace(full_text=brief)
        attachment = {"document_uuid": "d", "filename": "spec.docx"}
        with patch.object(
            decibyl.db_client, "get_document_by_uuid", AsyncMock(return_value=document)
        ):
            block = await decibyl.attached_block(ORG, [attachment], capped=True)
        assert brief.strip() in block and "showing the first" not in block


# --- through a real Decibyl turn ----------------------------------------------


def _session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@contextmanager
def _turn(rows: list, drawer: list[str]):
    """Everything a Decibyl turn reads, replaced by plain values; the turn's
    own logic runs for real. ``drawer`` is the full tool list it holds."""
    reply_row = SimpleNamespace(id=1)
    with ExitStack() as stack:
        for p in (
            patch(
                "api.services.workflow.decibyl.build_context",
                new=AsyncMock(return_value="## Team\nnothing"),
            ),
            patch(
                "api.services.workflow.decibyl.office_context",
                new=AsyncMock(return_value=""),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.agent_events",
                new=AsyncMock(return_value=rows),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.async_session",
                return_value=_session(),
            ),
            patch(
                "api.services.agent_builder.settings.resolve_for_organization",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        provider="anthropic", model="m", api_key="k"
                    )
                ),
            ),
            patch(
                "api.services.workflow.decibyl.agent_timeline.record",
                new=AsyncMock(return_value=reply_row.id),
            ),
            patch("api.services.workflow.decibyl.reply_draft.clear", new=AsyncMock()),
            patch.object(
                decibyl, "office_tools", lambda org=None: [_tool(n) for n in drawer]
            ),
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[])
            ),
            patch(
                "api.services.reach.outside_tools.schemas", AsyncMock(return_value=[])
            ),
            patch(
                "api.services.knowledge_graph.spaced_recall.related_context",
                AsyncMock(return_value=""),
            ),
            patch(
                "api.services.knowledge_graph.decisions.note", AsyncMock(return_value=0)
            ),
            patch(
                "api.services.knowledge_graph.feed.remember_exchange",
                AsyncMock(return_value=False),
            ),
            patch("api.services.identity.mobile_push.announce_reply", AsyncMock()),
            patch("api.services.workflow.decibyl.reply_stop.clear", AsyncMock()),
        ):
            stack.enter_context(p)
        yield


def _event(actor: str, body: str, i: int = 0, at: datetime | None = None):
    return SimpleNamespace(
        actor=actor,
        kind="message",
        payload={"body": body},
        summary=body,
        at=at or datetime.now(UTC),
        id=i,
    )


class TestAnswersStillIncludeTheToolsPeopleAskedFor:
    """The no-regression test. Every lever on, and a person asks for something:
    the model must be holding the tool that does it."""

    @pytest.mark.asyncio
    async def _ask(self, text: str, only, *flags: str, rows=None, script=None):
        only(*flags)
        seen: list[dict] = []
        replies = list(script or [ModelReply(text="done")])

        async def stream(**kwargs):
            seen.append(
                {
                    "tools": [t["name"] for t in (kwargs.get("tools") or [])],
                    "system": kwargs["system"],
                    "messages": list(kwargs["conversation"].messages),
                }
            )
            return replies.pop(0) if len(replies) > 1 else replies[0]

        rows = rows if rows is not None else [_event("human", text)]
        with (
            _turn(rows, EVERY_TOOL),
            patch("api.services.agent_builder.client.stream", new=stream),
        ):
            await decibyl.answer(ORG, text)
        return seen

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "text, tool",
        [
            ("make a poster for our diwali sale", "make_images"),
            ("find leads for dentists in pune", "find_leads"),
            ("draft a purchase order for cement", "draft_document"),
            ("rank the suppliers in that table", "rank_table"),
            ("is this message a scam", "check_for_scam"),
            ("use check_bot on the front desk", "check_bot"),
            ("call my supplier", "call_for_me"),
            ("who owes me money", "who_owes_me"),
            ("run a python script on it", "run_script"),
            ("thanks", "propose_action"),
            ("what is the time", "recall"),
        ],
    )
    async def test_the_tool_is_in_the_model_s_hand(self, text, tool, only):
        seen = await self._ask(text, only, *LEVERS)
        assert tool in seen[0]["tools"], (text, seen[0]["tools"])

    @pytest.mark.asyncio
    async def test_a_quick_turn_is_offered_fewer_tools_than_a_full_one(self, only):
        lean = await self._ask("thanks", only, "lean_tools")
        full = await self._ask("thanks", only)
        assert len(lean[0]["tools"]) < len(full[0]["tools"])
        assert lean_tools.MORE_TOOLS in lean[0]["tools"]
        assert lean_tools.MORE_TOOLS not in full[0]["tools"]
        assert full[0]["tools"] == EVERY_TOOL

    @pytest.mark.asyncio
    async def test_a_turn_that_is_not_quick_is_offered_everything(self, only):
        text = "Please draft a reply to this complaint and plan the follow-up."
        seen = await self._ask(text, only, "lean_tools", "cheap_routing")
        assert seen[0]["tools"] == EVERY_TOOL

    @pytest.mark.asyncio
    async def test_the_model_can_ask_for_more_and_gets_all_of_them(self, only):
        script = [
            ModelReply(
                text="",
                tool_calls=(
                    ToolCall(id="m1", name=lean_tools.MORE_TOOLS, arguments={}),
                ),
            ),
            ModelReply(text="done"),
        ]
        seen = await self._ask("thanks", only, "lean_tools", script=script)
        assert lean_tools.MORE_TOOLS in seen[0]["tools"]
        assert seen[1]["tools"] == EVERY_TOOL, "the second round has the full list"
        result = seen[1]["messages"][-1]
        assert result["role"] == "tool" and "loaded" in str(result["content"])

    @pytest.mark.asyncio
    async def test_a_tool_the_thread_used_before_is_still_there(self, only):
        earlier = _event("agent", "Here are four options.", 2)
        earlier.payload = {
            "body": "Here are four options.",
            "tools_used": ["make_images"],
        }
        rows = [_event("human", "thanks", 3), earlier, _event("human", "a poster", 1)]
        seen = await self._ask("thanks", only, "lean_tools", rows=rows)
        assert "make_images" in seen[0]["tools"]

    @pytest.mark.asyncio
    async def test_when_the_thread_cannot_be_read_everything_is_offered(self, only):
        only("lean_tools")
        seen: list = []

        async def stream(**kwargs):
            seen.append([t["name"] for t in kwargs["tools"]])
            return ModelReply(text="done")

        calls = {"n": 0}

        async def events(**kwargs):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("db down")
            return [_event("human", "thanks")]

        with (
            _turn([], EVERY_TOOL),
            patch("api.services.workflow.decibyl.db_client.agent_events", events),
            patch("api.services.agent_builder.client.stream", new=stream),
        ):
            await decibyl.answer(ORG, "thanks")
        assert seen[0] == EVERY_TOOL, "an unreadable thread must not narrow the list"

    @pytest.mark.asyncio
    async def test_the_reply_row_records_what_the_turn_used_only_when_lean(self, only):
        script = [
            ModelReply(
                text="", tool_calls=(ToolCall(id="1", name="recall", arguments={}),)
            ),
            ModelReply(text="done"),
        ]

        async def run(*flags):
            only(*flags)
            replies = list(script)

            async def stream(**kwargs):
                return replies.pop(0)

            with (
                _turn([_event("human", "thanks")], EVERY_TOOL),
                patch("api.services.agent_builder.client.stream", new=stream),
                patch.object(
                    decibyl,
                    "_tool",
                    AsyncMock(return_value={"status": "success"}),
                ),
                patch(
                    "api.services.workflow.decibyl.agent_timeline.record",
                    new=AsyncMock(return_value=1),
                ) as record,
            ):
                await decibyl.answer(ORG, "thanks")
            return [c.kwargs["payload"] for c in record.await_args_list]

        off = await run()
        on = await run("lean_tools")
        marks = ("tools_used", "tools_kept", "tools_full")
        assert not any(k in p for p in off for k in marks)
        written = [p for p in on if "tools_used" in p]
        assert written and written[0]["tools_used"] == ["recall"]
        assert written[0]["tools_kept"] == [], "core tools are not extras"

    @pytest.mark.asyncio
    async def test_a_warm_thread_keeps_the_list_it_had_and_a_cold_one_starts_lean(
        self, only
    ):
        def thread(seconds_ago: int, payload: dict):
            earlier = _event(
                "agent",
                "Done.",
                2,
                at=datetime.now(UTC) - timedelta(seconds=seconds_ago),
            )
            earlier.payload = {"body": "Done.", **payload}
            return [_event("human", "thanks", 3), earlier, _event("human", "hi", 1)]

        warm_full = await self._ask(
            "thanks", only, "lean_tools", rows=thread(30, {"tools_full": True})
        )
        assert warm_full[0]["tools"] == EVERY_TOOL, "the cache holds the full list"
        cold_full = await self._ask(
            "thanks", only, "lean_tools", rows=thread(900, {"tools_full": True})
        )
        assert lean_tools.MORE_TOOLS in cold_full[0]["tools"], "cold: lean again"

        warm_extra = await self._ask(
            "thanks",
            only,
            "lean_tools",
            rows=thread(30, {"tools_kept": ["make_images"]}),
        )
        assert "make_images" in warm_extra[0]["tools"]
        cold_extra = await self._ask(
            "thanks",
            only,
            "lean_tools",
            rows=thread(900, {"tools_kept": ["make_images"]}),
        )
        assert "make_images" not in cold_extra[0]["tools"]
        assert lean_tools.MORE_TOOLS in cold_extra[0]["tools"]

    @pytest.mark.asyncio
    async def test_a_long_thread_is_shortened_but_keeps_its_ids(self, only):
        uuid = "3f2b8c1e-9d4a-4e6b-8a7c-1b2c3d4e5f60"
        rows = [_event("human", "make option 2 bigger", 99)]
        for i in range(30):
            actor = "agent" if i % 2 else "human"
            body = f"line {i} " + "pad " * 80
            if i == 21:
                body = f"[Images made: poster: option 2 {uuid}] " + "pad " * 80
            rows.append(_event(actor, body, i))
        seen_off = await self._ask("make option 2 bigger", only, rows=rows)
        seen_on = await self._ask(
            "make option 2 bigger", only, "history_cap", rows=rows
        )
        off_size = len(json.dumps(seen_off[0]["messages"]))
        on_size = len(json.dumps(seen_on[0]["messages"]))
        assert on_size < off_size / 2
        assert uuid in json.dumps(seen_on[0]["messages"])
        assert seen_on[0]["messages"][-1]["content"].endswith(
            "## Question\nmake option 2 bigger"
        )

    @pytest.mark.asyncio
    async def test_every_lever_off_is_the_turn_it_always_was(self, only):
        seen = await self._ask("thanks", only)
        assert seen[0]["tools"] == EVERY_TOOL
        assert not isinstance(seen[0]["system"], client.SplitSystem)
        assert not any(
            "Earlier in this thread" in str(m["content"]) for m in seen[0]["messages"]
        )


class TestTheFlagsAreRegisteredAndOff:
    @pytest.mark.parametrize("name", LEVERS)
    def test_each_is_a_known_flag_with_a_description_and_off(self, name):
        assert name in features.FLAGS
        assert features.describe(name) != name.replace("_", " ").capitalize()
        assert features.is_on(name) is False
