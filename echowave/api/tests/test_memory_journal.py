"""B2 (decision journal), B4 (connections surfaced), B6 (spaced recall).

Eval scenarios at the bottom: ``memory_decision_journal_why_we_chose``,
``memory_connection_only_over_threshold`` and
``memory_spaced_recall_only_with_an_event``.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.db import db_client
from api.services.agent_builder.client import ModelReply, ToolCall
from api.services.billing import events as billing_events
from api.services.evals import judge
from api.services.knowledge_graph import (
    connections,
    decisions,
    feed,
    quiet,
    recall,
    spaced_recall,
)
from api.services.knowledge_graph.client import Fact
from api.services.workflow import connected_tools, decibyl

ORG = 7
NOW = datetime(2026, 9, 14, 4, 30, tzinfo=UTC)


class TestTheJournal:
    def test_a_line_without_a_cue_costs_no_model_call(self):
        assert not decisions.looks_like_decision("please send the invoice for August")
        assert decisions.looks_like_decision("we went with Sharma for the printing")

    async def test_no_cue_means_no_model(self):
        with patch(
            "api.services.agent_builder.client.complete", AsyncMock()
        ) as complete:
            assert (
                await decisions.extract(ORG, "hello there, how are the numbers") == []
            )
        complete.assert_not_awaited()

    async def test_a_decision_is_noted_inferred_with_its_parts(self):
        remember = AsyncMock(return_value=4)
        graph = AsyncMock(return_value=True)
        raw = {
            "decisions": [
                {
                    "what": "printing supplier",
                    "chosen": "Sharma Printers",
                    "over": ["Patel Press"],
                    "reason": "Patel was slower",
                    "people": ["Ravi"],
                }
            ]
        }
        with (
            patch.object(
                decisions, "extract", AsyncMock(return_value=decisions.clean(raw))
            ),
            patch.object(db_client, "remember_organisation_facts", remember),
            patch.object(feed, "remember_decision", graph),
        ):
            noted = await decisions.note(
                ORG, "we went with Sharma for the printing, Patel was slower", at=NOW
            )
        assert noted == 1
        kwargs = remember.await_args.kwargs
        assert kwargs["status"] == "learned"
        assert kwargs["subject_type"] == "decision"
        assert kwargs["subject_key"] == "printing supplier"
        assert kwargs["facts"]["chose"] == "Sharma Printers"
        assert kwargs["facts"]["over"] == "Patel Press"
        assert kwargs["facts"]["because"] == "Patel was slower"
        assert kwargs["facts"]["who"] == "Ravi"
        assert kwargs["facts"]["on"] == "2026-09-14"
        assert "Sharma Printers over Patel Press" in graph.await_args.kwargs["line"]

    async def test_a_journal_that_cannot_write_never_raises(self):
        with (
            patch.object(
                decisions,
                "extract",
                AsyncMock(return_value=[decisions.Decision(what="x", chosen="y")]),
            ),
            patch.object(
                db_client,
                "remember_organisation_facts",
                AsyncMock(side_effect=OSError("db")),
            ),
        ):
            assert await decisions.note(ORG, "decided x") == 0

    async def test_recall_finds_a_decision_by_its_words_and_labels_it(self):
        rows = [
            SimpleNamespace(
                subject_key="printing supplier",
                key="chose",
                value="Sharma Printers",
                status="learned",
                confirmed_at=None,
                last_seen_at=NOW,
            ),
            SimpleNamespace(
                subject_key="printing supplier",
                key="because",
                value="Patel was slower",
                status="learned",
                confirmed_at=None,
                last_seen_at=NOW,
            ),
            SimpleNamespace(
                subject_key="school",
                key="chose",
                value="DPS",
                status="confirmed",
                confirmed_at=NOW,
                last_seen_at=NOW,
            ),
        ]
        with patch.object(db_client, "subject_facts", AsyncMock(return_value=rows)):
            out = await decisions.recall_decisions(
                ORG, "why did we pick that printing supplier"
            )
        assert len(out) == 1
        assert out[0]["status"] == "inferred"
        assert (
            "Sharma Printers" in out[0]["fact"] and "Patel was slower" in out[0]["fact"]
        )


def _fact(text, when=NOW - timedelta(days=3)):
    return Fact("u", text, when, None, when, ())


class TestConnections:
    def test_two_complaints_are_not_a_pattern_three_are(self):
        two = [_fact("Ravi complained the delivery was late")] * 2
        assert connections.repeated_counterpart(two) == []
        three = two + [_fact("Ravi said the parcel was damaged")]
        found = connections.repeated_counterpart(three)
        assert (
            len(found) == 1
            and "Ravi" in found[0].line
            and "3 complaints" in found[0].line
        )

    def test_an_expiry_near_a_trip_is_one_line(self):
        tasks = [
            SimpleNamespace(
                id=1, title="Passport expiry", due_at=NOW + timedelta(days=20)
            ),
            SimpleNamespace(id=2, title="Goa trip", due_at=NOW + timedelta(days=21)),
            SimpleNamespace(
                id=3, title="LIC premium due", due_at=NOW + timedelta(days=40)
            ),
        ]
        found = connections.expiry_meets_travel(tasks)
        assert len(found) == 1
        assert "Passport expiry" in found[0].line and "Goa trip" in found[0].line

    def test_a_person_linking_two_others(self):
        facts = [
            _fact("Arun introduced Meera to the tailor"),
            _fact("Arun went to school with Kiran"),
        ]
        found = connections.bridge(facts)
        assert any(
            c.line.startswith("Arun is connected to Kiran, Meera") for c in found
        )

    async def test_nothing_crossed_means_nothing_said_and_no_slot_used(self):
        claim = AsyncMock(return_value=True)
        deliver = AsyncMock()
        with (
            patch.object(
                quiet,
                "people_opted_in",
                AsyncMock(return_value={ORG: [SimpleNamespace(id=1)]}),
            ),
            patch.object(
                connections,
                "gather",
                AsyncMock(return_value=([_fact("Ravi paid")], [])),
            ),
            patch.object(billing_events, "charge_in_own_session", AsyncMock()),
            patch.object(quiet, "claim_daily_slot", claim),
            patch.object(connections, "deliver", deliver),
        ):
            counters = await connections.notice(now=NOW)
        assert counters["quiet"] == 1
        claim.assert_not_awaited()
        deliver.assert_not_awaited()

    async def test_a_connection_is_said_once_under_the_cap_and_the_graph_read_is_billed(
        self,
    ):
        facts = [_fact("Ravi complained the delivery was late")] * 3
        charge = AsyncMock()
        deliver = AsyncMock()
        redis_client = AsyncMock()
        redis_client.set = AsyncMock(side_effect=[True, None])
        with (
            patch.object(
                quiet,
                "people_opted_in",
                AsyncMock(return_value={ORG: [SimpleNamespace(id=1)]}),
            ),
            patch.object(connections, "gather", AsyncMock(return_value=(facts, []))),
            patch.object(billing_events, "charge_in_own_session", charge),
            patch.object(quiet, "claim_daily_slot", AsyncMock(return_value=True)),
            patch("redis.asyncio.from_url", AsyncMock(return_value=redis_client)),
            patch.object(connections, "deliver", deliver),
        ):
            first = await connections.notice(now=NOW)
            second = await connections.notice(now=NOW + timedelta(days=1))
        assert first["said"] == 1 and second["said"] == 0 and second["quiet"] == 1
        assert deliver.await_count == 1
        assert charge.await_args.kwargs["event"] == billing_events.KNOWLEDGE_ANSWER

    async def test_nobody_opted_in_reads_nothing(self):
        gather = AsyncMock()
        with (
            patch.object(quiet, "people_opted_in", AsyncMock(return_value={})),
            patch.object(connections, "gather", gather),
        ):
            await connections.notice(now=NOW)
        gather.assert_not_awaited()


def _asked(key, question, when=NOW - timedelta(days=10), twice=False):
    return SimpleNamespace(
        subject_key=key,
        key="question",
        value=question,
        first_seen_at=when,
        last_seen_at=when + timedelta(days=2) if twice else when,
    )


class TestSpacedRecall:
    async def test_recall_notes_what_was_asked(self):
        remember = AsyncMock()
        with patch.object(db_client, "remember_organisation_facts", remember):
            await spaced_recall.remember_asked(
                ORG,
                about="fridge warranty",
                question="when does the fridge warranty end",
            )
        kwargs = remember.await_args.kwargs
        assert (
            kwargs["subject_type"] == "asked"
            and kwargs["subject_key"] == "fridge warranty"
        )
        assert kwargs["status"] == "learned"

    async def test_asked_twice_is_not_resurfaced(self):
        rows = [
            _asked("fridge warranty", "warranty?", twice=True),
            _asked("tailor", "the tailor's name"),
        ]
        with patch.object(db_client, "subject_facts", AsyncMock(return_value=rows)):
            once = await spaced_recall.asked_once(ORG)
        assert [r.subject_key for r in once] == ["tailor"]

    async def test_a_line_that_mentions_it_gets_a_block_only_at_its_interval(self):
        rows = [_asked("fridge warranty", "when does the fridge warranty end")]
        redis_client = AsyncMock()
        redis_client.get = AsyncMock(side_effect=[None, f"1|{NOW.isoformat()}"])
        redis_client.set = AsyncMock()
        with (
            patch.object(db_client, "subject_facts", AsyncMock(return_value=rows)),
            patch("redis.asyncio.from_url", AsyncMock(return_value=redis_client)),
        ):
            block = await spaced_recall.related_context(
                ORG, "the fridge is making a noise again", now=NOW
            )
            again = await spaced_recall.related_context(
                ORG, "the fridge is still noisy", now=NOW + timedelta(hours=2)
            )
        assert block.startswith("## Asked before") and "fridge warranty" in block
        assert again == ""

    async def test_an_unrelated_line_gets_nothing(self):
        rows = [_asked("fridge warranty", "when does the fridge warranty end")]
        with patch.object(db_client, "subject_facts", AsyncMock(return_value=rows)):
            assert (
                await spaced_recall.related_context(
                    ORG, "send the August invoice", now=NOW
                )
                == ""
            )

    async def test_no_event_means_no_message(self):
        rows = [_asked("tailor", "the tailor's name for the sherwani")]
        deliver = AsyncMock()
        with (
            patch.object(
                quiet,
                "people_opted_in",
                AsyncMock(return_value={ORG: [SimpleNamespace(id=1)]}),
            ),
            patch.object(db_client, "subject_facts", AsyncMock(return_value=rows)),
            patch.object(db_client, "tasks_due_between", AsyncMock(return_value=[])),
            patch.object(spaced_recall, "deliver", deliver),
        ):
            counters = await spaced_recall.resurface(now=NOW)
        assert counters["quiet"] == 1
        deliver.assert_not_awaited()

    async def test_an_approaching_event_earns_one_line_under_the_cap(self):
        rows = [_asked("tailor", "the tailor's name for the sherwani")]
        tasks = [
            SimpleNamespace(
                organization_id=ORG,
                title="Diwali: collect the sherwani from the tailor",
                due_at=NOW + timedelta(days=4),
            )
        ]
        deliver = AsyncMock()
        with (
            patch.object(
                quiet,
                "people_opted_in",
                AsyncMock(return_value={ORG: [SimpleNamespace(id=1)]}),
            ),
            patch.object(db_client, "subject_facts", AsyncMock(return_value=rows)),
            patch.object(db_client, "tasks_due_between", AsyncMock(return_value=tasks)),
            patch.object(spaced_recall, "_due", AsyncMock(return_value=True)),
            patch.object(quiet, "claim_daily_slot", AsyncMock(return_value=True)),
            patch.object(spaced_recall, "deliver", deliver),
        ):
            counters = await spaced_recall.resurface(now=NOW)
        assert counters["said"] == 1
        body = deliver.await_args.args[1]
        assert "sherwani" in body and "tailor" in body and "You asked memory" in body


# The eval scenarios.


def _session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@contextmanager
def _thread(last_line: str):
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
                new=AsyncMock(
                    return_value=[
                        SimpleNamespace(
                            actor="human",
                            payload={"body": last_line},
                            summary=last_line,
                            at=datetime.now(UTC),
                        )
                    ]
                ),
            ),
            patch(
                "api.services.workflow.decibyl.db_client.async_session",
                return_value=_session(),
            ),
            patch(
                "api.services.agent_builder.settings.resolve_model",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        provider="openai", model="m", api_key="k"
                    )
                ),
            ),
            patch(
                "api.services.workflow.decibyl.agent_timeline.record", new=AsyncMock()
            ),
            patch("api.services.workflow.decibyl.reply_draft.clear", new=AsyncMock()),
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[])
            ),
            patch.object(spaced_recall, "related_context", AsyncMock(return_value="")),
            patch.object(decisions, "note", AsyncMock(return_value=0)),
        ):
            stack.enter_context(p)
        yield


def _transcript(user: str, agent: str) -> list[dict]:
    return [{"role": "user", "text": user}, {"role": "agent", "text": agent}]


@pytest.mark.asyncio
class TestEvalScenarios:
    async def test_memory_decision_journal_why_we_chose(self):
        """ "Why did we pick Sharma for the printing?" Recall finds the
        journal's decision, inferred, and Decibyl says the reason as what
        was said, not as settled."""
        ask = "why did we pick Sharma for the printing?"
        turn1 = ModelReply(
            text="",
            tool_calls=(
                ToolCall(
                    id="c1",
                    name=recall.TOOL_NAME,
                    arguments={"question": "printing supplier Sharma"},
                ),
            ),
        )
        turn2 = ModelReply(
            text="From what was said on 3 September: Sharma Printers was chosen over Patel Press because Patel was slower. That is noted as a decision, not yet confirmed."
        )
        journal = [
            {
                "fact": "Decided printing supplier: Sharma Printers over Patel Press, because Patel was slower",
                "status": "inferred",
                "from": "2026-09-03",
                "until": None,
                "recorded": "2026-09-03",
            }
        ]
        with (
            _thread(ask),
            patch(
                "api.services.agent_builder.client.stream",
                new=AsyncMock(side_effect=[turn1, turn2]),
            ),
            patch.object(recall, "search_facts", AsyncMock(return_value=[])),
            patch.object(
                decisions, "recall_decisions", AsyncMock(return_value=journal)
            ),
            patch.object(spaced_recall, "remember_asked", AsyncMock()),
            patch.object(
                billing_events, "charge_in_own_session", AsyncMock()
            ) as charge,
        ):
            body = await decibyl.answer(ORG, ask)
        assert charge.await_count == 1
        verdict = judge.phrase_checks(
            _transcript(ask, body),
            must_say=["Sharma", "Patel was slower", "not yet confirmed"],
            must_not_say=["definitely", "always"],
        )
        assert verdict is None, verdict

    async def test_memory_connection_only_over_threshold(self):
        """Two complaints about Ravi: silence. Three: one line, once."""
        assert (
            connections.find([_fact("Ravi complained the delivery was late")] * 2, [])
            == []
        )
        found = connections.find(
            [_fact("Ravi complained the delivery was late")] * 3, []
        )
        verdict = judge.phrase_checks(
            [{"role": "agent", "text": found[0].line}],
            must_say=["Ravi", "3 complaints"],
            must_not_say=[],
        )
        assert verdict is None, verdict
        assert len(found[0].line.splitlines()) == 1

    async def test_memory_spaced_recall_only_with_an_event(self):
        """The fridge warranty, asked about once: silence until the fridge
        is mentioned, then a clause, not an announcement."""
        rows = [_asked("fridge warranty", "when does the fridge warranty end")]
        with (
            patch.object(db_client, "subject_facts", AsyncMock(return_value=rows)),
            patch.object(spaced_recall, "_due", AsyncMock(return_value=True)),
        ):
            silent = await spaced_recall.related_context(
                ORG, "send the August invoice", now=NOW
            )
            block = await spaced_recall.related_context(
                ORG, "the fridge is making a noise again", now=NOW
            )
        assert silent == ""
        assert "mention only if it helps" in block and "fridge warranty" in block
