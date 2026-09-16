"""What Decibyl says it can reach, and what it can actually reach.

The founder asked it to send an email. It has ``GMAIL_SEND_EMAIL``, with
that tool's arguments in its schema list, and it had sent one that morning.
It answered that it had no tool that sends Gmail -- only one that makes a
draft -- and offered the draft instead.

Two things made that possible, and both are here.

The context block described a tool list the model was not looking at: it
said the app tools were names only and had to be loaded first, which has
been untrue for every tool that carries its own parameters since the sync
started choosing them. And the block never named a single tool, so a round
in which no tools are offered -- the round after a card, by design -- left
the model describing its own drawer from memory of an earlier turn.

So the block names what is in the drawer, and the rules say the block is
the answer. A model that is wrong about its own tools is worse than one
with no tools: the person is told, with confidence, that the thing they
watched work an hour ago cannot be done.
"""

from __future__ import annotations

from types import SimpleNamespace

from api.services.workflow import connected_tools
from api.services.workflow.decibyl import SYSTEM, office_tools


def _tool(slug: str, *, toolkit: str = "gmail"):
    return SimpleNamespace(
        id=1,
        tool_uuid=f"t-{slug}",
        name=slug.replace("_", " ").title(),
        description="A connected tool",
        category="composio",
        definition={
            "type": "composio",
            "config": {"tool_slug": slug, "toolkit": toolkit},
        },
    )


GMAIL = [
    _tool("GMAIL_SEND_EMAIL"),
    _tool("GMAIL_CREATE_EMAIL_DRAFT"),
    _tool("GMAIL_FETCH_EMAILS"),
    _tool("GMAIL_GET_CONTACTS"),
]


class TestTheBlockNamesTheTools:
    def test_the_tool_it_denied_having_is_named(self):
        """The regression, in one line."""
        assert "app_gmail_send_email" in connected_tools.apps_block(GMAIL)

    def test_every_tool_is_named(self):
        block = connected_tools.apps_block(GMAIL)
        for tool in GMAIL:
            assert connected_tools.function_name(tool) in block

    def test_a_write_is_named_as_a_write_and_a_read_as_a_read(self):
        block = connected_tools.apps_block(GMAIL)
        send = block.index("app_gmail_send_email")
        fetch = block.index("app_gmail_fetch_emails")
        assert "proposes a card" in block[:send]
        assert "runs now" in block[:fetch]
        # Not the same line: the whole point is that the two are told apart.
        assert block[:send].rindex("proposes a card") > block[:send].rindex("\n") - 1

    def test_the_apps_are_still_summarised(self):
        block = connected_tools.apps_block(GMAIL)
        assert "Connected: gmail" in block
        assert "4 tools" in block

    def test_tools_are_grouped_by_app(self):
        block = connected_tools.apps_block(
            GMAIL + [_tool("SLACK_SEND_MESSAGE", toolkit="slack")]
        )
        assert "app_slack_send_message" in block
        lines = [ln for ln in block.splitlines() if ln.startswith("- ")]
        assert any(ln.startswith("- slack") for ln in lines)
        assert any(ln.startswith("- gmail") for ln in lines)


class TestTheBlockDoesNotDescribeAListTheModelCannotSee:
    def test_it_never_tells_the_model_to_load_a_tool_first(self):
        """``load_tool`` is offered only when something is actually deferred,
        and never on a round with no tools at all. A standing instruction to
        call it is an instruction to call a tool that may not be there, and
        it told the model its arguments were hidden when they were not. The
        tools that do need loading say so in their own descriptions, where
        the model is looking when it can act on it."""
        block = connected_tools.apps_block(GMAIL)
        assert connected_tools.LOAD_TOOL_NAME not in block

    def test_it_says_the_list_is_the_whole_list(self):
        block = connected_tools.apps_block(GMAIL)
        assert "every tool you have" in block


class TestABigDrawerStillFitsInTheContext:
    def test_the_names_are_capped(self):
        many = [_tool(f"GMAIL_FETCH_{n}") for n in range(200)]
        block = connected_tools.apps_block(many)
        assert block.count("app_gmail_fetch_") == connected_tools.MAX_NAMED

    def test_writes_are_named_before_reads_when_it_cannot_name_them_all(self):
        """A person asks for a send far more often than for a fetch, and it
        is the send it denied having."""
        many = [_tool(f"GMAIL_FETCH_{n}") for n in range(connected_tools.MAX_NAMED)]
        block = connected_tools.apps_block(many + [_tool("GMAIL_SEND_EMAIL")])
        assert "app_gmail_send_email" in block

    def test_a_partial_list_does_not_claim_to_be_the_whole_list(self):
        """The claim earns the model's trust in the block; making it when it
        is false is how this bug is built a second time."""
        many = [_tool(f"GMAIL_FETCH_{n}") for n in range(connected_tools.MAX_NAMED + 5)]
        block = connected_tools.apps_block(many)
        assert "every tool you have" not in block
        assert "5 more" in block


class TestTheRulesNameEveryToolItHas:
    def test_each_of_decibyls_own_tools_is_in_the_rules(self):
        """A tool the model is given and the rules never mention is a tool it
        uses by guesswork. The rules are the only place its job is described;
        anything added to the drawer gets a line here or it is invisible."""
        missing = [t["name"] for t in office_tools() if t["name"] not in SYSTEM]
        assert not missing, f"tools with no rule: {missing}"

    def test_the_rules_say_the_context_decides_what_it_can_reach(self):
        assert "Connected apps" in SYSTEM


class TestABadRowDoesNotCostTheWholeBlock:
    def test_a_row_that_cannot_be_named_is_left_out_not_raised(self):
        """This block is read on every turn. A workspace that cannot say what
        it has is worse off than one with a tool missing from the list."""
        broken = SimpleNamespace(name=None, description=None, definition=None)
        block = connected_tools.apps_block(GMAIL + [broken])
        assert "app_gmail_send_email" in block
        assert "4 tools" in block
