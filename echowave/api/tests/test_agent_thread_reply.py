"""An agent's own thread answers the message, not the transcript.

Production, agent "Netoyed Outreach": the owner typed "Hi" and the agent
answered "Here's what's been said so far in this channel:" with a bullet recap
of every earlier message, each human line attributed to "Someone" -- and did it
again on the next "Hi" ("Someone: Hi (repeat)").

Two causes, both in how the turn was built rather than in any rule about
greetings:

* the thread was glued in front of the message with nothing saying which part
  was background and which was the request, so for a two-letter message the
  transcript *was* the request; and the message itself was also the newest
  line of that transcript, so the bot saw it twice;
* every person was "Someone", so the owner of the agent read, to the agent,
  like a stranger.

The model is mocked the way the rest of the channel-reply suite mocks it (the
turn executor is patched); what these tests pin is what the model is handed.
"""

from __future__ import annotations

from types import SimpleNamespace as N
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete

from api.db import db_client
from api.db.controls_models import MemberPreferencesModel
from api.db.shell_models import UserOnboardingModel
from api.services.workflow import channel_context as cc
from api.services.workflow import channel_reply
from api.tests.support.voice import clean, make_people

MOD = "api.services.workflow.channel_reply"
OWNER = 5
NAMES = {46: "Netoyed Outreach"}


def _human(i: int, body: str, author=OWNER):
    return N(
        id=i,
        actor="human",
        workflow_id=46,
        summary=body,
        payload={"body": body, "author_id": author, "direct": True},
    )


def _agent(i: int, body: str):
    return N(id=i, actor="agent", workflow_id=46, summary=body, payload={"body": body})


def _thread(latest: str):
    """The production thread, newest first, ending in ``latest``."""
    return [
        _human(4, latest),
        _agent(
            3,
            "Which regions should I search, and should I include private banks?",
        ),
        _human(2, "Find heads of digital at mid-size banks"),
        _agent(1, "Hello! I'm Netoyed Outreach."),
    ]


class TestTheTranscriptTheModelSees:
    def test_the_person_is_named_not_someone(self):
        out = cc.render(
            _thread("Hi"),
            NAMES,
            people={OWNER: "Nithish"},
            answering="Hi",
            asker_id=OWNER,
            direct=True,
        )
        assert "Someone" not in out
        assert "Nithish: Find heads of digital at mid-size banks" in out
        # And the bot is told that this is who it is talking to.
        assert "Nithish is the person writing to you now" in out

    def test_the_message_being_answered_is_not_also_a_transcript_line(self):
        out = cc.render(
            _thread("Hi"),
            NAMES,
            people={OWNER: "Nithish"},
            answering="Hi",
            asker_id=OWNER,
            direct=True,
        )
        assert "Nithish: Hi" not in out
        # Everything before it is still there.
        assert "Netoyed Outreach: Which regions should I search" in out

    def test_a_direct_chat_is_not_called_a_channel_and_is_framed_as_background(self):
        out = cc.render(_thread("Hi"), NAMES, answering="Hi", direct=True)
        assert "THIS CHANNEL" not in out
        assert "YOUR CHAT" in out
        assert "background, not a request" in out

    def test_an_unnamed_asker_is_the_person_writing_and_others_are_teammates(self):
        rows = [_human(9, "Hi"), _human(8, "Ping from Ravi", author=77)]
        out = cc.render(rows, NAMES, people={}, answering="Hi", asker_id=OWNER)
        assert "A teammate: Ping from Ravi" in out
        assert cc.UNNAMED_ASKER in out
        assert "Someone" not in out

    def test_the_asker_is_whoever_wrote_the_newest_row_when_it_is_the_message(self):
        assert cc.asker_of(_thread("Hi"), "Hi") == OWNER
        # A hand-off from another bot: the newest row is not the message.
        assert cc.asker_of(_thread("Hi"), "Sales agent said: over to you") is None


@pytest.fixture
async def people(test_engine):
    p = await make_people("thread-names")
    async with db_client.async_session() as session:
        # a: a name chosen in Settings, which wins over onboarding's.
        session.add(MemberPreferencesModel(user_id=p.a.id, preferred_name="Nithish"))
        session.add(UserOnboardingModel(user_id=p.a.id, preferred_name="N"))
        # c: named, but in another workspace.
        session.add(UserOnboardingModel(user_id=p.c.id, preferred_name="Outsider"))
        await session.commit()
    yield p
    async with db_client.async_session() as session:
        for model in (MemberPreferencesModel, UserOnboardingModel):
            await session.execute(
                delete(model).where(model.user_id.in_([p.a.id, p.b.id, p.c.id]))
            )
        await session.commit()
    await clean(p)


@pytest.mark.asyncio
class TestNamingPeople:
    async def test_members_are_named_by_the_name_they_chose_and_nothing_else(
        self, people
    ):
        async with db_client.async_session() as session:
            user_b = await session.get(type(people.b), people.b.id)
            user_b.email = f"b-private-{people.b.id}@example.com"
            await session.commit()
        rows = [
            _human(3, "hi", author=people.a.id),
            _human(2, "hello", author=people.b.id),
            _human(1, "psst", author=people.c.id),
        ]
        named = await cc._people_for(people.org, rows)
        # The owner's chosen name; the onboarding one is only a fallback.
        assert named == {people.a.id: "Nithish"}
        out = cc.render(rows, {}, people=named)
        # b has no name: a teammate, never their email. c is not a member of
        # this workspace, so their name does not resolve here at all.
        assert "A teammate: hello" in out
        assert "b-private" not in out
        assert "Outsider" not in out


@pytest.mark.asyncio
class TestReplyingInTheAgentsThread:
    async def _reply(self, latest: str, answer: str):
        session = N(revision=1)
        with (
            patch(
                f"{MOD}.db_client.get_workflow_by_id",
                new=AsyncMock(
                    return_value=N(id=46, name="Netoyed Outreach", organization_id=7)
                ),
            ),
            patch(
                f"{MOD}.db_client.create_workflow_run",
                new=AsyncMock(return_value=N(id=11)),
            ),
            patch(f"{MOD}.set_current_run_id"),
            patch(
                f"{MOD}.authorize_workflow_run_start",
                new=AsyncMock(return_value=N(has_quota=True, error_message=None)),
            ),
            patch(
                f"{MOD}.db_client.ensure_workflow_run_text_session",
                new=AsyncMock(return_value=session),
            ),
            patch(
                f"{MOD}.initialize_text_chat_session",
                new=AsyncMock(return_value=session),
            ),
            patch(
                f"{MOD}.execute_pending_text_chat_turn",
                new=AsyncMock(return_value=session),
            ),
            # The real thread renderer over the production rows.
            patch(f"{MOD}.channel_context.db_client") as ctx_db,
            patch(
                f"{MOD}.channel_context._people_for",
                new=AsyncMock(return_value={OWNER: "Nithish"}),
            ),
            patch(
                f"{MOD}.channel_context._window",
                new=AsyncMock(return_value=(cc.MAX_CHARS, cc.MAX_EVENTS)),
            ),
            patch(
                "api.services.skills.shelf.prompt_for_workflow",
                new=AsyncMock(return_value=""),
            ),
            patch(
                f"{MOD}.append_text_chat_user_message",
                new=AsyncMock(return_value=session),
            ) as appended,
            patch(f"{MOD}._last_assistant_text", return_value=answer),
            patch(f"{MOD}.agent_timeline.record", new=AsyncMock()) as record,
            patch(f"{MOD}.billing_events.charge_in_own_session", new=AsyncMock()),
        ):
            ctx_db.agent_events = AsyncMock(return_value=_thread(latest))
            ctx_db.get_all_workflows_for_listing = AsyncMock(
                return_value=[N(id=46, name="Netoyed Outreach")]
            )
            await channel_reply.answer_in_channel(46, None, latest)
        turn = appended.await_args.kwargs["user_text"]
        reply = [
            c for c in record.await_args_list if c.kwargs.get("kind") == "message"
        ][-1]
        return turn, reply.kwargs["payload"]["body"]

    async def test_a_greeting_is_handed_over_as_the_message_with_the_thread_as_background(
        self,
    ):
        greeting = (
            "Hi Nithish! Still need your regions and whether to include "
            "private banks to start the search."
        )
        turn, reply = await self._reply("Hi", greeting)
        # The message is last, under its own heading -- not one more line of
        # the transcript.
        assert turn.endswith(f"{channel_reply.MESSAGE_HEADING}\nHi")
        # The transcript is background and the bot is told not to recap it.
        assert channel_reply.REPLY_RULES in turn
        assert "Do not recap" in channel_reply.REPLY_RULES
        assert turn.index("WHAT HAS BEEN SAID") < turn.index("HOW TO ANSWER")
        # Seen once, as the message, never as "Someone: Hi (repeat)".
        assert turn.count("Hi") == turn.count(f"{channel_reply.MESSAGE_HEADING}\nHi")
        assert "Someone" not in turn
        # What the model said is what the thread shows: a greeting, no recap.
        assert reply == greeting
        assert "said so far" not in reply

    async def test_catch_me_up_still_gets_the_recap(self):
        recap = (
            "You asked me to find heads of digital at mid-size banks; I asked "
            "which regions and whether to include private banks."
        )
        turn, reply = await self._reply("catch me up", recap)
        assert turn.endswith(f"{channel_reply.MESSAGE_HEADING}\ncatch me up")
        # The whole thread is in front of the bot, named, for it to recap.
        assert "Nithish: Find heads of digital at mid-size banks" in turn
        assert "Netoyed Outreach: Which regions should I search" in turn
        # And the rule lets a recap through when one is asked for.
        assert '"catch me up"' in channel_reply.REPLY_RULES
        assert reply == recap

    async def test_a_message_with_no_thread_is_just_the_message(self):
        assert channel_reply.compose_turn(None, "Hi") == "Hi"
        assert channel_reply.compose_turn("", "Hi") == "Hi"
