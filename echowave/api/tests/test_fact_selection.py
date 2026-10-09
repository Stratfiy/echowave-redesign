"""Confirmed facts reach a prompt because the question needs them (context_v2).

Before: the rows came back most-seen first, the read stopped at two hundred,
and the prompt kept the first forty, with no idea what was being asked. A
fact the business confirmed once and asks about once -- the Pune branch's GST
number -- never reached the model when ninety-nine others were more popular.

Now: the bot's own instructions and the business's identity and rules
always, then what the question matches, and nothing unrelated; a later
correction supersedes what it corrects; another member's personal memory is
never read at all; and what was left out is said, with a search for it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from api.db.models import (
    OrganisationFactModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
)
from api.services import acting, features
from api.services.workflow import decibyl, fact_selection, organisation_memory

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
RARE = "27AAACK4411P1Z5"


def _fact(i, key, value, *, seen=1, days_ago=30, workflow_id=None, user_id=None):
    return SimpleNamespace(
        id=i,
        key=key,
        value=value,
        kind="fact",
        status="confirmed",
        times_seen=seen,
        workflow_id=workflow_id,
        user_id=user_id,
        source_run_id=None,
        confirmed_at=NOW - timedelta(days=days_ago),
        last_seen_at=NOW - timedelta(days=days_ago),
    )


def _hundred() -> list[SimpleNamespace]:
    """Ninety-nine popular facts, most-seen first, and one rare one last --
    the order the database returns them in."""
    rows = [
        _fact(
            i,
            f"service {i}",
            f"we offer service number {i} at the counter",
            seen=200 - i,
        )
        for i in range(99)
    ]
    rows.append(_fact(99, "GST number Pune branch", RARE, seen=1))
    return rows


@pytest.fixture
def v2_on():
    features.set_snapshot(
        {(fact_selection.FLAG, None): features.Override(enabled=True)}
    )
    yield
    features.clear_snapshot()


class TestRelevanceNotPopularity:
    def test_a_rare_relevant_fact_among_a_hundred_is_chosen(self):
        rows = _hundred()
        # What the prompt kept before: the most-seen forty, and not this.
        before = organisation_memory.remembered_block({r.key: r.value for r in rows})
        assert RARE not in before

        chosen = fact_selection.select(
            rows, "What is the GST number for our Pune branch?", now=NOW
        )
        assert RARE in [r.value for r in chosen.rows]
        assert chosen.rows[0].value == RARE

    def test_unrelated_facts_are_not_padding(self):
        rows = _hundred() + [_fact(100, "parking", "basement level 2", seen=500)]
        chosen = fact_selection.select(rows, "What is the GST number in Pune?", now=NOW)
        keys = [r.key for r in chosen.rows]
        assert "GST number Pune branch" in keys
        assert "parking" not in keys
        assert chosen.left_out == len(rows) - len(chosen.rows)

    def test_a_question_with_nothing_to_match_gets_the_most_seen_few(self):
        chosen = fact_selection.select(_hundred(), "hi", now=NOW)
        assert [r.id for r in chosen.rows] == list(range(fact_selection.GENERIC_FILL))

    def test_identity_rules_and_the_bots_own_always_come(self):
        rows = _hundred() + [
            _fact(200, "business name", "Kaveri Opticals"),
            _fact(201, "never discuss", "competitor prices"),
            _fact(202, "greeting", "say namaste first", workflow_id=7),
        ]
        chosen = fact_selection.select(rows, "Pune GST number?", now=NOW)
        keys = {r.key for r in chosen.rows}
        assert {"business name", "never discuss", "greeting"} <= keys
        assert "GST number Pune branch" in keys

    def test_names_and_numbers_rank_first(self):
        rows = [
            _fact(1, "refund terms", "refunds within 14 days", seen=50),
            _fact(2, "Ravi account", "Ravi pays on the 5th, refund via UPI", seen=1),
        ]
        chosen = fact_selection.select(rows, "When does Ravi get his refund?", now=NOW)
        assert chosen.rows[0].id == 2

    def test_a_period_prefers_what_was_confirmed_in_it(self):
        rows = [
            _fact(1, "holiday", "closed on Diwali", days_ago=90, seen=40),
            _fact(2, "new price list", "frames from Rs 1,200", days_ago=2),
            _fact(3, "warranty", "one year on frames", days_ago=3),
        ]
        chosen = fact_selection.select(rows, "What did we confirm this week?", now=NOW)
        assert {r.id for r in chosen.rows} == {2, 3}

    def test_indian_script_words_match(self):
        rows = _hundred() + [_fact(300, "डिलीवरी समय", "शाम पाँच बजे तक", seen=1)]
        chosen = fact_selection.select(rows, "डिलीवरी कब तक होगी?", now=NOW)
        assert "शाम पाँच बजे तक" in [r.value for r in chosen.rows]


class TestCorrectionsAndScopes:
    def test_a_later_correction_supersedes_the_old_value(self):
        rows = [
            _fact(1, "opening hours", "9 to 6", seen=30, days_ago=40),
            _fact(2, "Opening_Hours", "10 to 7", seen=1, days_ago=1),
        ]
        chosen = fact_selection.select(rows, "What are the opening hours?", now=NOW)
        assert [r.value for r in chosen.rows] == ["10 to 7"]

    def test_the_narrower_scope_wins_whatever_its_age(self):
        rows = [
            _fact(1, "reply language", "English", days_ago=1),
            _fact(2, "reply language", "Hindi", days_ago=60, workflow_id=7),
        ]
        chosen = fact_selection.select(rows, "Which language?", now=NOW)
        assert [r.value for r in chosen.rows] == ["Hindi"]


class TestWhatIsLeftOutIsSaid:
    def test_the_block_names_its_sources_and_the_search(self):
        rows = _hundred()
        rows[-1].source_run_id = 4411
        chosen = fact_selection.select(rows, "Pune GST?", now=NOW)
        text = fact_selection.block(chosen)
        assert f"GST number Pune branch: {RARE} (fact 99" in text
        assert "from run 4411" in text
        assert "more confirmed facts not shown here" in text
        assert fact_selection.TOOL_NAME in text

    def test_the_agents_block_counts_what_it_left_out(self):
        block = organisation_memory.remembered_block({"hours": "9 to 6"}, left_out=12)
        assert "and 12 more confirmed facts on record, not shown here" in block


# --- against the database -------------------------------------------------------


async def _org(session, slug):
    org = OrganizationModel(provider_id=f"org-facts-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    users = []
    for who in ("owner", "colleague"):
        user = UserModel(
            provider_id=f"user-facts-{slug}-{who}", selected_organization_id=org.id
        )
        session.add(user)
        users.append(user)
    await session.flush()
    bot = WorkflowModel(
        name=f"Desk {slug}", organization_id=org.id, user_id=users[0].id
    )
    session.add(bot)
    await session.flush()
    return org, users[0], users[1], bot


def _row(
    org, key, value, *, seen=1, user_id=None, workflow_id=None, status="confirmed"
):
    return OrganisationFactModel(
        organization_id=org.id,
        subject_type="organisation",
        subject_key="self",
        key=key,
        value=value,
        kind="fact",
        status=status,
        times_seen=seen,
        user_id=user_id,
        workflow_id=workflow_id,
        confirmed_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
class TestAgainstTheDatabase:
    async def test_a_rare_fact_past_the_popular_read_is_found(
        self, db_session, async_session
    ):
        org, owner, _, _ = await _org(async_session, "rare")
        async_session.add_all(
            [
                _row(org, f"service {i}", f"counter service {i}", seen=1000 - i)
                for i in range(fact_selection.CANDIDATE_LIMIT + 50)
            ]
            + [_row(org, "GST number Pune branch", RARE, seen=1)]
        )
        await async_session.flush()

        _rows, text = await fact_selection.for_question(
            org.id, "What's the GST number for Pune?", user_id=owner.id
        )
        assert RARE in text
        assert "counter service 7" not in text

    async def test_another_members_private_fact_is_never_read(
        self, db_session, async_session
    ):
        org, owner, colleague, bot = await _org(async_session, "private")
        async_session.add_all(
            [
                _row(org, "Ravi salary", "Rs 40,000 a month", user_id=colleague.id),
                _row(org, "Ravi phone", "98450 11223"),
            ]
        )
        await async_session.flush()

        _, owners = await fact_selection.for_question(
            org.id, "What is Ravi's salary?", user_id=owner.id
        )
        assert "Rs 40,000" not in owners
        assert "98450 11223" in owners

        _, theirs = await fact_selection.for_question(
            org.id, "What is Ravi's salary?", user_id=colleague.id
        )
        assert "Rs 40,000" in theirs and "the asker's own" in theirs

        searched = await fact_selection.for_thread(
            org.id, {"query": "Ravi salary"}, user_id=owner.id
        )
        assert all("40,000" not in f["value"] for f in searched["facts"])

        remembered, _ = await fact_selection.remembered_for_bot(
            org.id, bot.id, "Ravi salary"
        )
        assert "Rs 40,000 a month" not in remembered.values()

    async def test_a_correction_written_over_the_fact_is_what_is_read(
        self, db_session, async_session
    ):
        from api.db import db_client

        org, owner, _, _ = await _org(async_session, "correct")
        await db_client.remember_organisation_facts(
            organization_id=org.id, facts={"delivery radius": "5 km"}
        )
        await db_client.remember_organisation_facts(
            organization_id=org.id, facts={"delivery radius": "8 km"}
        )
        _, text = await fact_selection.for_question(
            org.id, "How far do you deliver?", user_id=owner.id
        )
        assert "delivery radius: 8 km" in text
        assert "5 km" not in text

    async def test_unconfirmed_facts_stay_out(self, db_session, async_session):
        org, owner, _, _ = await _org(async_session, "learned")
        async_session.add_all(
            [
                _row(org, "parking", "overheard: free parking", status="learned"),
                _row(org, "parking fee", "Rs 50 an hour"),
            ]
        )
        await async_session.flush()
        _, text = await fact_selection.for_question(
            org.id, "Is parking free?", user_id=owner.id
        )
        assert "Rs 50 an hour" in text
        assert "overheard" not in text

    async def test_decibyl_reads_the_chosen_facts_and_holds_the_search(
        self, db_session, async_session, v2_on, monkeypatch
    ):
        from unittest.mock import AsyncMock

        from api.services.workflow import connected_tools, files_search

        async def no_embeddings(organization_id):
            return {}

        monkeypatch.setattr(files_search, "_embeddings", no_embeddings)
        monkeypatch.setattr(
            connected_tools, "awaiting_setup", AsyncMock(return_value=[])
        )
        org, owner, _, _ = await _org(async_session, "decibyl")
        async_session.add_all(
            [
                _row(org, f"service {i}", f"counter service {i}", seen=1000 - i)
                for i in range(100)
            ]
            + [_row(org, "GST number Pune branch", RARE, seen=1)]
        )
        await async_session.flush()

        with acting.acting_as(owner.id):
            context = await decibyl.build_context(
                org.id, "What's our GST number in Pune?"
            )
        memory = context.split("## What the business has confirmed\n", 1)[1]
        memory = memory.split("\n## ", 1)[0]
        assert RARE in memory
        assert "counter service" not in memory
        assert "more confirmed facts not shown here" in memory

        assert fact_selection.TOOL_NAME in [
            t["name"] for t in decibyl.office_tools(org.id)
        ]
        assert fact_selection.TOOL_NAME in decibyl.system_prompt(org.id)

    async def test_off_is_the_old_block(self, db_session, async_session):
        org, owner, _, _ = await _org(async_session, "off")
        async_session.add(_row(org, "opening hours", "9 to 6"))
        await async_session.flush()
        with acting.acting_as(owner.id):
            context = await decibyl.build_context(org.id, "hours?")
        assert "- opening hours: 9 to 6\n" in context
        assert fact_selection.TOOL_NAME not in [
            t["name"] for t in decibyl.office_tools(org.id)
        ]


@pytest.mark.asyncio
class TestTheAgentReadsChosenFacts:
    async def test_the_agent_prompt_carries_its_own_and_counts_the_rest(
        self, db_session, async_session, simple_workflow, v2_on
    ):
        from unittest.mock import AsyncMock, patch

        from pipecat.processors.aggregators.llm_context import LLMContext

        from api.services.workflow.pipecat_engine import PipecatEngine
        from pipecat.tests import MockLLMService

        org, _, _, bot = await _org(async_session, "engine")
        async_session.add_all(
            [
                _row(org, f"service {i}", f"counter service {i}", seen=1000 - i)
                for i in range(60)
            ]
            + [_row(org, "greeting", "say namaste first", workflow_id=bot.id)]
        )
        await async_session.flush()

        engine = PipecatEngine(
            llm=MockLLMService(mock_steps=[]),
            context=LLMContext(),
            workflow=simple_workflow,
            call_context_vars={},
            workflow_run_id=1,
            task=SimpleNamespace(),
            is_voice=True,
        )
        engine._organization_id = org.id
        engine._workflow_id = bot.id
        seen: dict[str, str] = {}

        async def capture(system_prompt, functions):
            seen["prompt"] = system_prompt

        engine._update_llm_context = capture
        with patch.object(engine, "_get_timezone", new=AsyncMock(return_value=None)):
            await engine._setup_llm_context(
                simple_workflow.nodes[simple_workflow.start_node_id]
            )
        prompt = seen["prompt"]
        assert "- greeting: say namaste first" in prompt
        assert "more confirmed facts on record, not shown here" in prompt
