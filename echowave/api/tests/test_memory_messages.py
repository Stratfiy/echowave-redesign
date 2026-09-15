"""B3 (the Sunday review) and B5 (teach it back), and the two rules over
everything memory says unasked: a per-person switch and one message a day.

Eval scenarios: ``memory_teach_it_back`` and ``memory_sunday_review_quiet_week``
at the bottom, scripted against Decibyl and the composer with the judge's
phrase checks.
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
from api.services.knowledge_graph import client as graph_client
from api.services.knowledge_graph import feed, quiet, recall, sunday_review, teach
from api.services.knowledge_graph.client import Fact
from api.services.workflow import connected_tools, decibyl

ORG = 7
AT = datetime(2026, 9, 13, 3, 30, tzinfo=UTC)  # a Sunday


class TestTheSwitches:
    async def test_everything_is_off_until_asked(self):
        with patch.object(
            db_client, "get_user_configuration_value", AsyncMock(return_value=None)
        ):
            assert await quiet.switches_for(1) == {
                "sunday_review": False,
                "connections": False,
                "spaced_recall": False,
            }

    async def test_turning_one_on_leaves_the_others(self):
        store: dict = {}

        async def get(user_id, key):
            return store.get((user_id, key))

        async def put(user_id, key, value):
            store[(user_id, key)] = value
            return value

        with (
            patch.object(db_client, "get_user_configuration_value", get),
            patch.object(db_client, "upsert_user_configuration_value", put),
        ):
            await quiet.set_switch(1, quiet.SUNDAY_REVIEW, True)
            out = await quiet.set_switch(1, quiet.CONNECTIONS, False)
        assert out == {
            "sunday_review": True,
            "connections": False,
            "spaced_recall": False,
        }
        assert store[(1, quiet.KEY)]["sunday_review"] is True

    async def test_the_tool_needs_to_know_who_is_asking(self):
        two = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
        with patch.object(
            db_client, "get_organization_users", AsyncMock(return_value=two)
        ):
            out = await quiet.for_thread(
                ORG, None, {"switch": "sunday_review", "on": True}
            )
        assert out["status"] == "error" and "Settings" in out["error"]

    async def test_a_one_person_workspace_is_that_person(self):
        one = [SimpleNamespace(id=9)]
        with (
            patch.object(
                db_client, "get_organization_users", AsyncMock(return_value=one)
            ),
            patch.object(
                quiet, "set_switch", AsyncMock(return_value={"sunday_review": True})
            ) as set_,
        ):
            out = await quiet.for_thread(
                ORG, None, {"switch": "sunday_review", "on": True}
            )
        assert out["status"] == "success"
        assert set_.await_args.args == (9, "sunday_review", True)

    async def test_an_unknown_switch_is_refused(self):
        out = await quiet.for_thread(ORG, 1, {"switch": "everything", "on": True})
        assert out["status"] == "error"


class TestOneMessageADay:
    async def test_the_second_claim_of_a_day_loses(self):
        client = AsyncMock()
        client.set = AsyncMock(side_effect=[True, None])
        with patch("redis.asyncio.from_url", AsyncMock(return_value=client)):
            assert await quiet.claim_daily_slot(ORG, now=AT) is True
            assert await quiet.claim_daily_slot(ORG, now=AT) is False
        key = client.set.await_args_list[0].args[0]
        assert key == f"{quiet.DAILY_PREFIX}{ORG}:2026-09-13"

    async def test_no_redis_means_send_not_silence(self):
        with patch("redis.asyncio.from_url", AsyncMock(side_effect=OSError("down"))):
            assert await quiet.claim_daily_slot(ORG, now=AT) is True

    async def test_the_document_reminders_share_the_cap(self):
        from api.services.workflow import document_fields

        with patch.object(quiet, "claim_daily_slot", AsyncMock(return_value=False)):
            assert await document_fields._already_sent_today(ORG) is True


def _fact(text, status="inferred", when=AT - timedelta(days=2)):
    return Fact("u", text, when, None, when, (), status=status)


class TestTheReview:
    def test_a_week_ends_now_and_starts_seven_days_before(self):
        start, end = sunday_review.week_of(AT)
        assert end == AT and end - start == timedelta(days=7)

    async def test_a_quiet_week_is_one_line_and_no_model(self):
        complete = AsyncMock()
        with patch("api.services.agent_builder.client.complete", complete):
            body = await sunday_review.compose(ORG, sunday_review.Material(facts=[]))
        assert body == sunday_review.QUIET_LINE
        assert body.count("\n") == 0
        complete.assert_not_awaited()

    async def test_a_written_review_is_under_fifteen_lines(self):
        long = "\n".join(f"line {i}" for i in range(30))
        material = sunday_review.Material(
            facts=[_fact("Ravi said he would pay by Friday")]
        )
        with (
            patch("api.services.workflow.decibyl.db_client.async_session"),
            patch(
                "api.services.agent_builder.settings.resolve_model",
                AsyncMock(
                    return_value=SimpleNamespace(
                        provider="openai", model="m", api_key="k"
                    )
                ),
            ),
            patch(
                "api.services.agent_builder.client.complete",
                AsyncMock(return_value=ModelReply(text=long)),
            ),
        ):
            body = await sunday_review.compose(ORG, material)
        assert len(body.splitlines()) == sunday_review.MAX_LINES

    async def test_a_model_that_fails_still_gives_the_plain_review(self):
        material = sunday_review.Material(
            facts=[_fact("Ravi said he would pay by Friday")],
            due=[SimpleNamespace(title="LIC premium", due_at=AT + timedelta(days=5))],
        )
        with patch(
            "api.services.agent_builder.settings.resolve_model",
            AsyncMock(side_effect=RuntimeError("no key")),
        ):
            body = await sunday_review.compose(ORG, material)
        assert "Ravi said he would pay by Friday (said, not confirmed)" in body
        assert "LIC premium" in body
        assert len(body.splitlines()) <= sunday_review.MAX_LINES

    async def test_the_graph_read_is_a_knowledge_answer_and_the_cap_holds(self):
        people = {
            ORG: [SimpleNamespace(id=1, email="meera@clinic.in", email_verified_at=AT)]
        }
        charge = AsyncMock()
        deliver = AsyncMock()
        with (
            patch.object(quiet, "people_opted_in", AsyncMock(return_value=people)),
            patch.object(quiet, "claim_daily_slot", AsyncMock(return_value=True)),
            patch.object(
                sunday_review,
                "gather",
                AsyncMock(return_value=sunday_review.Material(facts=[_fact("x")])),
            ),
            patch.object(
                sunday_review,
                "compose",
                AsyncMock(return_value="Added this week:\n- x"),
            ),
            patch.object(billing_events, "charge_in_own_session", charge),
            patch.object(sunday_review, "deliver", deliver),
        ):
            counters = await sunday_review.send_reviews(now=AT)
        assert counters["sent"] == 1
        assert charge.await_args.kwargs["event"] == billing_events.KNOWLEDGE_ANSWER
        assert charge.await_args.kwargs["ref_id"] == f"sunday_review:{ORG}:2026-09-13"
        assert deliver.await_args.kwargs["people"] == people[ORG]

    async def test_no_graph_means_no_graph_charge(self):
        people = {ORG: [SimpleNamespace(id=1, email=None, email_verified_at=None)]}
        charge = AsyncMock()
        with (
            patch.object(quiet, "people_opted_in", AsyncMock(return_value=people)),
            patch.object(quiet, "claim_daily_slot", AsyncMock(return_value=True)),
            patch.object(
                sunday_review,
                "gather",
                AsyncMock(return_value=sunday_review.Material(facts=None)),
            ),
            patch.object(billing_events, "charge_in_own_session", charge),
            patch.object(sunday_review, "deliver", AsyncMock()),
        ):
            await sunday_review.send_reviews(now=AT)
        charge.assert_not_awaited()

    async def test_an_account_that_already_heard_from_memory_today_waits(self):
        people = {ORG: [SimpleNamespace(id=1)]}
        deliver = AsyncMock()
        with (
            patch.object(quiet, "people_opted_in", AsyncMock(return_value=people)),
            patch.object(quiet, "claim_daily_slot", AsyncMock(return_value=False)),
            patch.object(sunday_review, "deliver", deliver),
        ):
            counters = await sunday_review.send_reviews(now=AT)
        assert counters["capped"] == 1
        deliver.assert_not_awaited()

    async def test_nobody_opted_in_means_nothing_sent(self):
        with patch.object(quiet, "people_opted_in", AsyncMock(return_value={})):
            counters = await sunday_review.send_reviews(now=AT)
        assert counters == {"considered": 0, "sent": 0, "capped": 0, "failed": 0}


class TestTeachingItBack:
    def test_a_subject_is_one_key_however_it_is_typed(self):
        assert teach.subject_key("Arun Mehta") == teach.subject_key("  arun   mehta ")

    async def test_a_correction_is_a_confirmed_record_fact_and_a_graph_episode(self):
        remember = AsyncMock(return_value=1)
        correction = AsyncMock(return_value=True)
        record = AsyncMock()
        with (
            patch.object(db_client, "remember_organisation_facts", remember),
            patch.object(feed, "remember_correction", correction),
            patch("api.services.knowledge_graph.teach.agent_timeline.record", record),
        ):
            out = await teach.correct(
                ORG,
                {
                    "subject": "Arun",
                    "key": "relationship",
                    "value": "college",
                    "was": "work",
                },
                ref_id="r1",
            )
        assert out["status"] == "success"
        assert out["changed"] == "Arun · relationship: work → college (confirmed)"
        kwargs = remember.await_args.kwargs
        assert kwargs["status"] == "confirmed"
        assert kwargs["subject_type"] == "person" and kwargs["subject_key"] == "arun"
        assert kwargs["facts"] == {"relationship": "college"}
        assert correction.await_args.kwargs["was"] == "work"
        assert "Corrected: Arun" in record.await_args.kwargs["summary"]

    async def test_half_a_correction_is_refused(self):
        with patch.object(
            db_client, "remember_organisation_facts", AsyncMock()
        ) as remember:
            out = await teach.correct(ORG, {"subject": "Arun"}, ref_id="r")
        assert out["status"] == "error"
        remember.assert_not_awaited()

    async def test_the_graph_hears_the_correction_as_confirmed_text(self):
        remember = AsyncMock(return_value=True)
        with (
            patch.object(graph_client, "KNOWLEDGE_GRAPH_URL", "falkor://here"),
            patch.object(feed.ingest, "remember", remember),
        ):
            assert (
                await feed.remember_correction(
                    organization_id=ORG,
                    subject="Arun",
                    key="relationship",
                    value="college",
                    was="work",
                    at=AT,
                )
                is True
            )
        episode = remember.await_args.args[0]
        assert (
            "Correction confirmed by the person: Arun — relationship: college."
            in episode.body
        )
        assert "Not work" in episode.body
        assert episode.group_id == "org:7" and episode.reference_time == AT

    async def test_recall_puts_the_record_first_and_leaves_closed_edges_out(self):
        """The confirmed fact outranks the inferred one: it is listed first,
        and the edge the correction closed (invalid_at) is not in a
        present-tense answer at all."""
        graph = AsyncMock()
        graph.search.return_value = [
            SimpleNamespace(
                uuid="old",
                fact="Arun is a colleague from work",
                valid_at=AT - timedelta(days=30),
                invalid_at=AT - timedelta(days=1),
                created_at=AT - timedelta(days=30),
                episodes=[],
            ),
            SimpleNamespace(
                uuid="other",
                fact="Arun lives in Pune",
                valid_at=AT - timedelta(days=10),
                invalid_at=None,
                created_at=AT - timedelta(days=10),
                episodes=[],
            ),
        ]
        row = SimpleNamespace(
            id=5, key="relationship", value="college", confirmed_at=AT
        )
        with (
            patch.object(graph_client, "_client", graph),
            patch.object(graph_client, "KNOWLEDGE_GRAPH_URL", "falkor://here"),
            patch.object(db_client, "organisation_memory", AsyncMock(return_value=[])),
            patch.object(db_client, "subject_facts", AsyncMock(return_value=[row])),
            patch.object(billing_events, "charge_in_own_session", AsyncMock()),
        ):
            out = await recall.for_thread(
                ORG, {"question": "who is Arun", "about": "Arun"}, ref_id="r"
            )
        facts = [f["fact"] for f in out["facts"]]
        assert facts[0] == "Arun — relationship: college"
        assert out["facts"][0]["status"] == "confirmed"
        assert "Arun is a colleague from work" not in facts
        assert "Arun lives in Pune" in facts


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
        ):
            stack.enter_context(p)
        yield


def _transcript(user: str, agent: str) -> list[dict]:
    return [{"role": "user", "text": user}, {"role": "agent", "text": agent}]


@pytest.mark.asyncio
class TestEvalScenarios:
    async def test_memory_teach_it_back(self):
        """ "No, Arun is from college, not work": the correction is written
        as confirmed, the graph hears it, and Decibyl says what changed in
        one line."""
        ask = "no, Arun is from college, not work"
        turn1 = ModelReply(
            text="",
            tool_calls=(
                ToolCall(
                    id="c1",
                    name=teach.TOOL_NAME,
                    arguments={
                        "subject": "Arun",
                        "key": "relationship",
                        "value": "college",
                        "was": "work",
                    },
                ),
            ),
        )
        turn2 = ModelReply(
            text="Noted. Arun · relationship: work → college (confirmed)."
        )
        remember = AsyncMock(return_value=1)
        correction = AsyncMock(return_value=True)
        with (
            _thread(ask),
            patch(
                "api.services.agent_builder.client.stream",
                new=AsyncMock(side_effect=[turn1, turn2]),
            ),
            patch.object(db_client, "remember_organisation_facts", remember),
            patch.object(feed, "remember_correction", correction),
            patch(
                "api.services.knowledge_graph.teach.agent_timeline.record", AsyncMock()
            ),
        ):
            body = await decibyl.answer(ORG, ask, author_id=1)
        assert remember.await_args.kwargs["status"] == "confirmed"
        assert correction.await_count == 1
        assert len(body.strip().splitlines()) == 1
        verdict = judge.phrase_checks(
            _transcript(ask, body),
            must_say=["Arun", "college", "confirmed"],
            must_not_say=["I think", "probably"],
        )
        assert verdict is None, verdict

    async def test_memory_sunday_review_quiet_week(self):
        """A week in which nothing happened is one line, and the person who
        did not turn the review on hears nothing at all."""
        body = await sunday_review.compose(ORG, sunday_review.Material(facts=[]))
        verdict = judge.phrase_checks(
            [{"role": "agent", "text": body}],
            must_say=["Quiet week"],
            must_not_say=["Added this week"],
        )
        assert verdict is None, verdict
        assert len(body.splitlines()) == 1

    async def test_memory_messages_are_turned_on_from_the_thread(self):
        ask = "send me the sunday review"
        turn1 = ModelReply(
            text="",
            tool_calls=(
                ToolCall(
                    id="c1",
                    name=quiet.TOOL_NAME,
                    arguments={"switch": "sunday_review", "on": True},
                ),
            ),
        )
        turn2 = ModelReply(
            text="Done: the Sunday review is now on for you. It comes once a week, and only if there is something to say."
        )
        set_ = AsyncMock(
            return_value={
                "sunday_review": True,
                "connections": False,
                "spaced_recall": False,
            }
        )
        with (
            _thread(ask),
            patch(
                "api.services.agent_builder.client.stream",
                new=AsyncMock(side_effect=[turn1, turn2]),
            ),
            patch.object(quiet, "set_switch", set_),
        ):
            body = await decibyl.answer(ORG, ask, author_id=42)
        assert set_.await_args.args == (42, "sunday_review", True)
        verdict = judge.phrase_checks(
            _transcript(ask, body), must_say=["Sunday review", "on"], must_not_say=[]
        )
        assert verdict is None, verdict
