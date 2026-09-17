"""Asked for a draft, a send never becomes a card.

#365 put a derived line on the card saying what Confirm does, and it works.
It does not stop the model choosing the wrong tool, and the SYSTEM rule
added alongside it was tried against the live account and did not change the
behaviour: asked to "create a draft reply -- do not send anything", Decibyl
proposed GMAIL_REPLY_TO_THREAD again, and again called it a draft that sends
nothing. A prompt rule is advice. This is the refusal.

Read in the safe direction on purpose. A false positive costs one round and
gets a draft instead of a send. A false negative is mail the person said not
to send.
"""

from __future__ import annotations

from types import SimpleNamespace

from api.enums import ToolCategory
from api.services.workflow import draft_requests


def tool(slug: str) -> SimpleNamespace:
    return SimpleNamespace(
        tool_uuid=f"uuid-{slug.lower()}",
        name=slug,
        description=slug.replace("_", " ").title(),
        status="active",
        category=ToolCategory.COMPOSIO.value,
        definition={
            "type": "composio",
            "config": {"tool_slug": slug, "toolkit": slug.split("_")[0].lower()},
        },
    )


GMAIL = [
    tool("GMAIL_FETCH_EMAILS"),
    tool("GMAIL_SEND_EMAIL"),
    tool("GMAIL_SEND_DRAFT"),
    tool("GMAIL_REPLY_TO_THREAD"),
    tool("GMAIL_CREATE_EMAIL_DRAFT"),
]

THE_REAL_REQUEST = (
    "Fetch my most recent unread Gmail message by its message id so you have "
    "the full body, the thread id and the sender, then create a draft reply "
    "to it. Create the draft only - do not send anything."
)


class TestTheRequestThatCausedThis:
    def test_the_send_is_refused(self):
        out = draft_requests.refusal(
            text=THE_REAL_REQUEST, tool=tool("GMAIL_REPLY_TO_THREAD"), tools=GMAIL
        )
        assert out is not None
        assert out["status"] == "refused"

    def test_it_names_the_tool_to_use_instead(self):
        out = draft_requests.refusal(
            text=THE_REAL_REQUEST, tool=tool("GMAIL_REPLY_TO_THREAD"), tools=GMAIL
        )
        assert "GMAIL_CREATE_EMAIL_DRAFT" in out["reason"]

    def test_the_draft_tool_itself_goes_through(self):
        assert (
            draft_requests.refusal(
                text=THE_REAL_REQUEST,
                tool=tool("GMAIL_CREATE_EMAIL_DRAFT"),
                tools=GMAIL,
            )
            is None
        )

    def test_a_read_goes_through(self):
        """The same turn has to fetch the message first."""
        assert (
            draft_requests.refusal(
                text=THE_REAL_REQUEST, tool=tool("GMAIL_FETCH_EMAILS"), tools=GMAIL
            )
            is None
        )

    def test_sending_a_draft_is_still_a_send(self):
        """The trap: SEND_DRAFT has the word in it and posts the mail."""
        out = draft_requests.refusal(
            text=THE_REAL_REQUEST, tool=tool("GMAIL_SEND_DRAFT"), tools=GMAIL
        )
        assert out is not None


class TestWhenNobodyAskedForADraft:
    def test_an_ordinary_send_is_untouched(self):
        assert (
            draft_requests.refusal(
                text="Email Priya the quote for the Hosur job.",
                tool=tool("GMAIL_SEND_EMAIL"),
                tools=GMAIL,
            )
            is None
        )

    def test_another_apps_write_is_untouched(self):
        assert (
            draft_requests.refusal(
                text="Post this on LinkedIn.",
                tool=tool("LINKEDIN_CREATE_LINKED_IN_POST"),
                tools=GMAIL,
            )
            is None
        )


class TestDoNotSendIsAbsolute:
    def test_it_holds_with_no_draft_tool_on_the_account(self):
        """ "Do not send" is not a request for a different tool. It is the
        thing the person does not want, and no tool choice makes it fine."""
        out = draft_requests.refusal(
            text="Reply to that thread but do not send it.",
            tool=tool("GMAIL_REPLY_TO_THREAD"),
            tools=[tool("GMAIL_REPLY_TO_THREAD")],
        )
        assert out is not None
        assert "no draft tool" in out["reason"]

    def test_the_model_is_told_to_say_so(self):
        out = draft_requests.refusal(
            text="Reply but don't send.",
            tool=tool("GMAIL_REPLY_TO_THREAD"),
            tools=[tool("GMAIL_REPLY_TO_THREAD")],
        )
        assert "say that to the person" in out["reason"].lower()

    def test_several_spellings(self):
        for said in ("do not send", "don't send", "dont send", "without sending"):
            assert draft_requests.said_do_not_send(f"Reply to Priya, {said}.")


class TestTheDraftAlternativeIsPerApp:
    def test_gmails_draft_is_not_offered_for_zoho(self):
        """A draft in the wrong app is its own wrong answer."""
        assert (
            draft_requests.draft_tool_for(tool("ZOHO_MAIL_MESSAGES_SEND"), GMAIL)
            is None
        )

    def test_the_apps_own_draft_is_found(self):
        found = draft_requests.draft_tool_for(tool("GMAIL_REPLY_TO_THREAD"), GMAIL)
        assert found.definition["config"]["tool_slug"] == "GMAIL_CREATE_EMAIL_DRAFT"


class TestReadingTheWords:
    def test_draft_is_a_whole_word(self):
        assert draft_requests.wants_a_draft("write a draft")
        assert draft_requests.wants_a_draft("start drafting a reply")
        assert not draft_requests.wants_a_draft("the draughtsman called")

    def test_an_empty_request_asks_for_nothing(self):
        assert not draft_requests.wants_a_draft("")
        assert not draft_requests.said_do_not_send("")


class TestItIsActuallyWiredIn:
    """The module passing its own tests proves nothing. What matters is that
    the path which turned a send into a card now asks first."""

    @staticmethod
    def _call_for(slug: str):
        """Named the way the real dispatch names it, by asking the same
        function the code asks rather than guessing the format."""
        from api.services.workflow import connected_tools

        name = next(
            key
            for key, value in connected_tools.by_function_name(GMAIL).items()
            if connected_tools.slug_of(value) == slug
        )
        return SimpleNamespace(
            name=name,
            id="call-1",
            arguments={"thread_id": "t1", "message_body": "Noted."},
        )

    async def test_the_send_never_reaches_a_card(self):
        from unittest.mock import AsyncMock, patch

        from api.services.workflow import connected_tools, decibyl

        with (
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=GMAIL)
            ),
            patch.object(decibyl.actions, "propose", AsyncMock()) as proposed,
        ):
            out = await decibyl._app_tool(
                1,
                self._call_for("GMAIL_REPLY_TO_THREAD"),
                request=THE_REAL_REQUEST,
            )
        assert out["status"] == "refused"
        assert "GMAIL_CREATE_EMAIL_DRAFT" in out["reason"]
        proposed.assert_not_awaited()

    async def test_an_ordinary_send_still_gets_its_card(self):
        from unittest.mock import AsyncMock, patch

        from api.services.workflow import connected_tools, decibyl

        with (
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=GMAIL)
            ),
            patch.object(
                decibyl.actions,
                "propose",
                AsyncMock(return_value={"state": "proposed"}),
            ) as proposed,
        ):
            out = await decibyl._app_tool(
                1,
                self._call_for("GMAIL_SEND_EMAIL"),
                request="Email Priya the quote please.",
            )
        assert out == {"state": "proposed"}
        proposed.assert_awaited()

    async def test_a_turn_with_no_request_text_is_unchanged(self):
        """Every other caller of _app_tool passes nothing, and must keep
        working exactly as it did."""
        from unittest.mock import AsyncMock, patch

        from api.services.workflow import connected_tools, decibyl

        with (
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=GMAIL)
            ),
            patch.object(
                decibyl.actions,
                "propose",
                AsyncMock(return_value={"state": "proposed"}),
            ) as proposed,
        ):
            out = await decibyl._app_tool(1, self._call_for("GMAIL_SEND_EMAIL"))
        assert out == {"state": "proposed"}
        proposed.assert_awaited()


class TestARefusalLeavesTheModelAbleToFix:
    """The refusal tells the model which tool to use instead. That advice is
    worth nothing if the refusal also takes its tools away.

    ``reads_only`` decides whether the next round is offered tools at all. A
    round that wrote a card ends the turn's tool access, which is right: the
    card is the outcome. A refusal wrote nothing -- no card, no effect, and
    the model has been handed a correction it is expected to act on. Counting
    it as a write meant the model was told to use GMAIL_CREATE_EMAIL_DRAFT
    and then offered no tools to do it with, and the turn ended on the canned
    "I have nothing to add on that." Observed live after #366 shipped.
    """

    def test_a_refusal_keeps_the_tools(self):
        from api.services.workflow import connected_tools, decibyl

        call = SimpleNamespace(name=f"{connected_tools.PREFIX}gmail_reply_to_thread")
        assert decibyl._was_a_read(call, {"status": "refused", "reason": "..."})

    def test_a_card_still_ends_them(self):
        """Unchanged: a round that proposed something is the turn's outcome."""
        from api.services.workflow import connected_tools, decibyl

        call = SimpleNamespace(name=f"{connected_tools.PREFIX}gmail_send_email")
        assert not decibyl._was_a_read(call, {"state": "proposed"})

    def test_a_read_is_still_a_read(self):
        from api.services.workflow import connected_tools, decibyl

        call = SimpleNamespace(name=f"{connected_tools.PREFIX}gmail_fetch_emails")
        assert decibyl._was_a_read(call, {"status": "success", "data": {}})
