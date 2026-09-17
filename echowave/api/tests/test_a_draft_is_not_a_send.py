"""A routine may write a draft. It may not send one.

The unattended gate began as one line: reads on a schedule, writes only with
a person there. That line was right about sending and wrong about drafting,
and the difference is not a nuance -- it is most of what an overnight bot is
for. A draft sits in the account's own drafts folder. Nobody receives it,
nothing is announced, and the only way it reaches another person is that the
operator opens it in the morning and presses send. It is the human review
the gate exists to require, expressed as a tool.

Classified by an explicit list of slugs rather than a verb, because the verb
is ``CREATE`` and so is the verb on half the ways to change the world.
"""

from __future__ import annotations

from types import SimpleNamespace

from api.enums import ToolCategory
from api.services.workflow import connected_tools, unattended


def composio(slug: str) -> SimpleNamespace:
    return SimpleNamespace(
        category=ToolCategory.COMPOSIO.value,
        tool_uuid=f"uuid-{slug.lower()}",
        definition={
            "type": "composio",
            "config": {"tool_slug": slug, "toolkit": slug.split("_")[0].lower()},
        },
    )


class TestWhatCountsAsStaged:
    def test_a_gmail_draft_is_staged(self):
        assert unattended.is_staged(composio("GMAIL_CREATE_EMAIL_DRAFT"))

    def test_a_zoho_draft_is_staged(self):
        assert unattended.is_staged(composio("ZOHO_MAIL_MESSAGES_CREATE_DRAFT"))

    def test_sending_a_draft_is_not_staged(self):
        """The trap. Same object, same noun, opposite act -- SEND_DRAFT is
        the button the operator is supposed to press themselves."""
        assert not unattended.is_staged(composio("GMAIL_SEND_DRAFT"))

    def test_sending_mail_is_not_staged(self):
        assert not unattended.is_staged(composio("GMAIL_SEND_EMAIL"))

    def test_replying_to_a_thread_is_not_staged(self):
        """A reply reaches the other person the moment it is made."""
        assert not unattended.is_staged(composio("GMAIL_REPLY_TO_THREAD"))

    def test_another_apps_create_is_not_staged(self):
        """CREATE is not the test. A calendar event with guests on it is a
        message to every one of them."""
        assert not unattended.is_staged(composio("GOOGLECALENDAR_CREATE_EVENT"))
        assert not unattended.is_staged(composio("LINKEDIN_CREATE_LINKED_IN_POST"))

    def test_a_read_is_not_staged(self):
        """Staged means 'a write we allow', not 'anything allowed'. A read is
        allowed by being a read, and must not be double-counted."""
        assert not unattended.is_staged(composio("GMAIL_FETCH_EMAILS"))

    def test_a_non_composio_tool_is_not_staged(self):
        assert not unattended.is_staged(
            SimpleNamespace(category=ToolCategory.HTTP_API.value, definition={})
        )

    def test_an_unknown_slug_is_not_staged(self):
        """Unknown means no, the same direction is_read() takes."""
        assert not unattended.is_staged(composio("GMAIL_INVENT_SOMETHING"))


class TestStagedIsStillAWrite:
    """The new class must not quietly reclassify anything for everyone else.

    ``is_read`` decides ordering, the reserved write slots and the context
    block's two lists. A draft that started reporting itself as a read would
    take a read's slot and change what every bot is offered, on a call as
    much as on a schedule.
    """

    def test_a_draft_is_still_not_a_read(self):
        assert not connected_tools.is_read(composio("GMAIL_CREATE_EMAIL_DRAFT"))


DRAFT = composio("GMAIL_CREATE_EMAIL_DRAFT")
SEND = composio("GMAIL_SEND_EMAIL")
FETCH = composio("GMAIL_FETCH_EMAILS")


def _manager(*, gated: bool):
    from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager

    manager = CustomToolManager(SimpleNamespace(_workflow_run_id=1))
    manager._writes_gated = gated
    return manager


class TestWhatAnUnattendedRunIsOffered:
    """The bug this fixes, concretely: a bot built to draft replies overnight
    was given GMAIL_CREATE_EMAIL_DRAFT, and the gate took it away again at
    8am for being a write. The deliverable was an apology."""

    async def test_a_draft_survives_the_gate(self):
        kept = await _manager(gated=True)._minus_ungated_writes([FETCH, DRAFT])
        assert kept == [FETCH, DRAFT]

    async def test_a_send_still_does_not(self):
        kept = await _manager(gated=True)._minus_ungated_writes([FETCH, DRAFT, SEND])
        assert kept == [FETCH, DRAFT]

    async def test_everything_stays_when_a_person_is_there(self):
        kept = await _manager(gated=False)._minus_ungated_writes([FETCH, DRAFT, SEND])
        assert kept == [FETCH, DRAFT, SEND]

    async def test_a_bot_that_only_drafts_never_pays_for_the_lookup(self):
        """A list whose every write is staged has nothing to gate, so it must
        not cost a run-and-workflow read to find that out."""
        from unittest.mock import AsyncMock, patch

        manager = _manager(gated=True)
        manager._writes_gated = None
        with patch.object(unattended, "run_is_unattended", AsyncMock()) as asked:
            kept = await manager._minus_ungated_writes([FETCH, DRAFT])
        asked.assert_not_awaited()
        assert kept == [FETCH, DRAFT]


class TestABotThatDraftsIsGivenTheDraftTool:
    """Letting the gate pass a draft is useless if nothing attaches one.

    Gmail's writes rank SEND_EMAIL, SEND_DRAFT, REPLY_TO_THREAD,
    CREATE_EMAIL_DRAFT. With three write slots the draft tool is exactly one
    place past the cut, so every bot built from a brief got three ways to
    send mail and no way to draft it.

    Reserved rather than reordered. Putting CREATE above SEND would undo
    #344, where a bot told to reply got three ways to make a draft and no way
    to send one. Both tools want to exist; neither ordering gives both.
    """

    @staticmethod
    def _gmail_writes():
        return [
            composio("GMAIL_SEND_EMAIL"),
            composio("GMAIL_SEND_DRAFT"),
            composio("GMAIL_REPLY_TO_THREAD"),
            composio("GMAIL_CREATE_EMAIL_DRAFT"),
        ]

    def test_the_draft_tool_is_attached(self):
        from api.services.workflow import brief_apps

        kept = brief_apps.tool_uuids(
            "read gmail and draft replies", self._gmail_writes()
        )
        assert composio("GMAIL_CREATE_EMAIL_DRAFT").tool_uuid in kept

    def test_the_send_tool_is_still_attached(self):
        """The reserved slot costs the lowest-ranked write that made the cut,
        not the highest. A bot on a call must still be able to send."""
        from api.services.workflow import brief_apps

        kept = brief_apps.tool_uuids(
            "read gmail and draft replies", self._gmail_writes()
        )
        assert composio("GMAIL_SEND_EMAIL").tool_uuid in kept

    def test_the_ranking_itself_is_untouched(self):
        """#344's guard. The swap happens at the cap; SEND must still outrank
        CREATE in the ordering, or a bot told to reply gets three drafts."""
        from api.services.workflow import brief_apps

        ranked = brief_apps.write_tool_uuids("gmail", self._gmail_writes())
        send = composio("GMAIL_SEND_EMAIL").tool_uuid
        draft = composio("GMAIL_CREATE_EMAIL_DRAFT").tool_uuid
        assert ranked.index(send) < ranked.index(draft)

    def test_an_app_with_no_draft_tool_is_unchanged(self):
        """Nothing to reserve means nothing reserved -- a LinkedIn bot must
        not lose a write to a slot held open for a tool that cannot exist."""
        from api.services.workflow import brief_apps

        tools = [
            composio("LINKEDIN_CREATE_LINKED_IN_POST"),
            composio("LINKEDIN_CREATE_COMMENT_ON_POST"),
            composio("LINKEDIN_CREATE_ARTICLE_OR_URL_SHARE"),
            composio("LINKEDIN_CREATE_VIDEO_POST"),
        ]
        kept = brief_apps.tool_uuids("post to linkedin", tools)
        # All four: with no reads to keep, the writes borrow the whole cap.
        assert len(kept) == 4
