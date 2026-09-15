"""The question cards on Home come from this account's own life."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.services.workflow import home_openers

MONDAY_MORNING = datetime(2026, 9, 14, 9, 30)
TUESDAY_AFTERNOON = datetime(2026, 9, 15, 14, 0)
TUESDAY_EVENING = datetime(2026, 9, 15, 19, 0)

BOTS = [
    {"workflow_id": 1, "name": "Front desk", "calls": 9, "is_live": True},
    {"workflow_id": 2, "name": "Chaser", "calls": 2, "is_live": True},
]


class TestANewAccount:
    def test_gets_the_first_jobs_for_its_business(self):
        cards = home_openers.build(members=[], recent_questions=[], business="clinic")
        assert cards[0]["text"] == "Answer my clinic's phone and book appointments"
        assert all(c["kind"] == "first_job" for c in cards)
        assert len(cards) == 4

    def test_a_business_we_cannot_guess_a_job_for_gets_the_default_list(self):
        for business in (None, "", "software", "galaxy"):
            cards = home_openers.build(
                members=[], recent_questions=[], business=business
            )
            assert [c["text"] for c in cards] == list(
                home_openers.DEFAULT_FIRST_JOBS
            ), business


class TestAnAccountWithALife:
    def test_what_they_asked_last_comes_first(self):
        cards = home_openers.build(
            members=BOTS,
            recent_questions=["Which bots took calls today?", "Draft a reply to Meera"],
            now=TUESDAY_AFTERNOON,
        )
        assert [c["text"] for c in cards[:2]] == [
            "Which bots took calls today?",
            "Draft a reply to Meera",
        ]
        assert cards[0]["kind"] == "asked_before"

    def test_at_most_two_echoes_and_never_a_handoff_line(self):
        cards = home_openers.build(
            members=BOTS,
            recent_questions=["@front are we open?", "one", "two", "three"],
            now=TUESDAY_AFTERNOON,
        )
        echoes = [c["text"] for c in cards if c["kind"] == "asked_before"]
        assert echoes == ["one", "two"]

    def test_a_long_question_is_clipped_on_the_card(self):
        long = "Please tell me " + "x" * 200
        cards = home_openers.build(
            members=BOTS, recent_questions=[long], now=TUESDAY_AFTERNOON
        )
        assert len(cards[0]["text"]) == home_openers.CARD_CHARS
        assert cards[0]["text"].endswith("…")

    def test_missed_calls_the_board_and_the_busiest_bot(self):
        cards = home_openers.build(
            members=BOTS,
            recent_questions=[],
            unreturned_missed_calls=3,
            stuck_tasks=1,
            now=TUESDAY_AFTERNOON,
        )
        assert [c["text"] for c in cards] == [
            "Call back the 3 people who rang and got nobody",
            "What is stuck on the task board?",
            "How did Front desk do this week?",
            "What happened since yesterday?",
        ]

    def test_the_time_question_follows_the_clock(self):
        quiet = dict(members=BOTS, recent_questions=[])
        assert home_openers.build(**quiet, now=MONDAY_MORNING)[1]["text"] == (
            "What happened last week?"
        )
        assert home_openers.build(**quiet, now=TUESDAY_EVENING)[1]["text"] == (
            "What needs closing before tomorrow?"
        )

    def test_never_more_than_four_and_never_a_repeat(self):
        cards = home_openers.build(
            members=BOTS,
            recent_questions=[
                "What needs my attention today?",
                "what needs my attention today",
            ],
            unreturned_missed_calls=2,
            stuck_tasks=3,
            now=TUESDAY_AFTERNOON,
        )
        texts = [c["text"] for c in cards]
        assert len(cards) == 4
        assert len({t.casefold() for t in texts}) == 4
        assert texts.count("What needs my attention today?") == 1


@pytest.mark.asyncio
class TestTheReads:
    async def test_only_the_persons_own_lines_distinct_newest_first(self):
        rows = [
            SimpleNamespace(actor="human", payload={"body": "Two"}, summary=""),
            SimpleNamespace(actor="decibyl", payload={"body": "reply"}, summary=""),
            SimpleNamespace(actor="human", payload={"body": "two"}, summary=""),
            SimpleNamespace(actor="human", payload={}, summary="One"),
        ]
        with patch(
            "api.services.workflow.home_openers.db_client.agent_events",
            new=AsyncMock(return_value=rows),
        ) as events:
            assert await home_openers.recent_questions(7) == ["Two", "One"]
        assert events.await_args.kwargs["assistant_thread"] is True

    async def test_a_read_that_fails_is_no_card_not_no_home(self):
        with (
            patch(
                "api.services.workflow.home_openers.db_client.agent_events",
                new=AsyncMock(side_effect=RuntimeError("down")),
            ),
            patch(
                "api.services.workflow.home_openers.db_client.tasks_for_organization",
                new=AsyncMock(side_effect=RuntimeError("down")),
            ),
        ):
            cards = await home_openers.gather(
                7, members=BOTS, unreturned_missed_calls=0, now=TUESDAY_AFTERNOON
            )
        assert [c["text"] for c in cards][:2] == [
            "How did Front desk do this week?",
            "What happened since yesterday?",
        ]

    async def test_a_new_account_reads_the_door(self):
        with patch(
            "api.services.workflow.home_openers.db_client.subject_facts",
            new=AsyncMock(
                return_value=[
                    SimpleNamespace(key="role", value="owner"),
                    SimpleNamespace(key="business", value="logistics"),
                ]
            ),
        ) as facts:
            cards = await home_openers.gather(7, members=[], unreturned_missed_calls=0)
        assert facts.await_args.kwargs["subject_type"] == "door"
        assert cards[0]["text"] == "Quote shipments from our rate card"


@pytest.mark.asyncio
class TestTheDoorRoute:
    async def test_the_answers_are_kept_on_the_account(self):
        from api.app import app
        from api.services.auth.depends import get_user

        app.dependency_overrides[get_user] = lambda: SimpleNamespace(
            id=42, selected_organization_id=7
        )
        try:
            with patch(
                "api.services.workflow.home_openers.db_client.remember_organisation_facts",
                new=AsyncMock(return_value=2),
            ) as remember:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        "/api/v1/onboarding/door",
                        json={"role": "owner", "business": "clinic", "heard": "friend"},
                    )
        finally:
            app.dependency_overrides.pop(get_user, None)
        assert response.status_code == 200, response.text
        kwargs = remember.await_args.kwargs
        assert kwargs["organization_id"] == 7
        assert kwargs["facts"] == {"role": "owner", "business": "clinic"}
        assert kwargs["subject_type"] == "door"


def test_decibyl_is_told_who_it_is_talking_to():
    from api.services.workflow import decibyl

    assert decibyl.door_block({}) == (
        "They have not said. Ask, if it matters to the answer."
    )
    assert decibyl.door_block({"role": "owner", "business": "real_estate"}) == (
        "Signing up, they said their role: owner; their business: real estate."
    )


class TestDecibylKnowsTheFiles:
    """An account with three documents filed against bots was told its
    knowledge base was empty, while looking at them on the Knowledge base
    screen. The search was empty; the files were not."""

    def test_says_what_each_file_is_for(self):
        from api.services.workflow import decibyl

        rows = [
            SimpleNamespace(
                filename="rates.pdf",
                scope="bot",
                workflow_id=3,
                processing_status="completed",
            ),
            SimpleNamespace(
                filename="policy.docx",
                scope="org",
                workflow_id=None,
                processing_status="completed",
            ),
            SimpleNamespace(
                filename="huge.pdf",
                scope="library",
                workflow_id=None,
                processing_status="processing",
            ),
        ]
        block = decibyl.documents_block(rows, {3: "Front desk"})
        assert "rates.pdf: Front desk's own" in block
        assert "policy.docx: Company knowledge, read by every bot" in block
        assert "huge.pdf: the library" in block
        assert "(still being read)" in block

    def test_an_empty_account_says_so_rather_than_nothing(self):
        from api.services.workflow import decibyl

        assert decibyl.documents_block([], {}) == "Nothing uploaded yet."
