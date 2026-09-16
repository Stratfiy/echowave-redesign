"""What the Integrations screen says an app does, against what it gives you.

The screen lists an app's tools one line each so that somebody deciding
whether to connect Gmail "can see that it means reading, searching and
sending mail rather than a number in a corner" -- the route's own words.

It asked the vendor for twelve. The vendor returns them alphabetically, the
fact ``tool_sync`` was written around: the first twelve of Facebook's forty
begin ASSIGN_PAGE_TASK, and the first twelve of Gmail's sixty are seven ways
to delete mail. So the screen showed a list nobody would connect an app for,
while connecting it created a ranked twelve with the sends and the searches
in them. Two different dozens under one heading, and the one on the screen
was the worse one.

The catalogue this reads and the rows a sync creates are now chosen by the
same function over the same sample. A screen that promises what connecting
does not deliver is a broken promise either way round.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api.services.integrations.composio import tool_sync


def _action(slug: str) -> dict:
    return {"slug": slug, "does": f"does {slug}"}


#: Facebook's catalogue as the vendor returns it: alphabetical, with the
#: page-and-message actions a business connects for sorted well past twelve.
FACEBOOK = [
    _action(s)
    for s in (
        "FACEBOOK_ASSIGN_PAGE_TASK",
        "FACEBOOK_CREATE_COMMENT",
        "FACEBOOK_CREATE_MULTI_PHOTO_POST",
        "FACEBOOK_DELETE_COMMENT",
        "FACEBOOK_DELETE_PAGE_PHOTO",
        "FACEBOOK_DELETE_POST",
        "FACEBOOK_GET_COMMENT",
        "FACEBOOK_GET_COMMENTS",
        "FACEBOOK_GET_CONVERSATION_MESSAGES",
        "FACEBOOK_GET_CURRENT_USER",
        "FACEBOOK_GET_MESSAGE_DETAILS",
        "FACEBOOK_GET_PAGE_CONVERSATIONS",
        "FACEBOOK_GET_PAGE_DETAILS",
        "FACEBOOK_GET_PAGE_INSIGHTS",
        "FACEBOOK_SEND_MEDIA_MESSAGE",
        "FACEBOOK_SEND_MESSAGE",
    )
]


def _vendor(actions: list[dict]):
    """The vendor, honestly: alphabetical, and it honours ``limit``.

    A fake that ignores the limit hides this bug completely -- the route
    would receive the whole catalogue it never asked for and look correct.
    """
    ordered = sorted(actions, key=lambda a: a["slug"])
    calls: list[int] = []

    async def fake(slug, limit=None):
        calls.append(limit)
        return ordered[:limit] if limit else ordered

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


class TestTheScreenShowsTheRankedDozen:
    @pytest.mark.asyncio
    async def test_it_asks_for_the_whole_sample_not_the_first_dozen(self):
        """A ranking cannot improve on a sample it never sees. This is the
        same reason ``ACTIONS_CONSIDERED`` exists for the sync."""
        from api.routes import connectors

        actions = _vendor(FACEBOOK)
        with (
            patch.object(connectors, "is_configured", return_value=True),
            patch.object(
                connectors, "toolkit_name", AsyncMock(return_value="Facebook")
            ),
            patch.object(connectors, "toolkit_actions", actions),
        ):
            await connectors.list_app_tools(slug="facebook", user=object())
        assert actions.calls == [tool_sync.ACTIONS_CONSIDERED]

    @pytest.mark.asyncio
    async def test_the_send_a_person_connects_for_is_on_the_screen(self):
        from api.routes import connectors

        with (
            patch.object(connectors, "is_configured", return_value=True),
            patch.object(
                connectors, "toolkit_name", AsyncMock(return_value="Facebook")
            ),
            patch.object(connectors, "toolkit_actions", _vendor(FACEBOOK)),
        ):
            shown = await connectors.list_app_tools(slug="facebook", user=object())
        slugs = [t.slug for t in shown.tools]
        assert "FACEBOOK_SEND_MESSAGE" in slugs
        assert "FACEBOOK_GET_PAGE_CONVERSATIONS" in slugs

    @pytest.mark.asyncio
    async def test_the_deletes_do_not_take_the_screen(self):
        """Not banned -- last. An app of nothing but deletes still shows
        them, for the reason ``most_useful`` gives: banned looks like
        broken."""
        from api.routes import connectors

        with (
            patch.object(connectors, "is_configured", return_value=True),
            patch.object(
                connectors, "toolkit_name", AsyncMock(return_value="Facebook")
            ),
            patch.object(connectors, "toolkit_actions", _vendor(FACEBOOK)),
        ):
            shown = await connectors.list_app_tools(slug="facebook", user=object())
        slugs = [t.slug for t in shown.tools]
        assert not [s for s in slugs if "DELETE" in s]

    @pytest.mark.asyncio
    async def test_the_screen_and_the_sync_show_the_same_dozen(self):
        """The claim this file exists to keep: what the screen promises is
        what connecting delivers."""
        from api.routes import connectors

        with (
            patch.object(connectors, "is_configured", return_value=True),
            patch.object(
                connectors, "toolkit_name", AsyncMock(return_value="Facebook")
            ),
            patch.object(connectors, "toolkit_actions", _vendor(FACEBOOK)),
        ):
            shown = await connectors.list_app_tools(slug="facebook", user=object())
        created = tool_sync.most_useful(FACEBOOK, tool_sync.MAX_PER_APP)
        assert [t.slug for t in shown.tools] == [a["slug"] for a in created]

    @pytest.mark.asyncio
    async def test_it_still_shows_at_most_the_dozen(self):
        from api.routes import connectors

        many = [_action(f"FACEBOOK_GET_THING_{n}") for n in range(90)]
        with (
            patch.object(connectors, "is_configured", return_value=True),
            patch.object(
                connectors, "toolkit_name", AsyncMock(return_value="Facebook")
            ),
            patch.object(connectors, "toolkit_actions", _vendor(many)),
        ):
            shown = await connectors.list_app_tools(slug="facebook", user=object())
        assert len(shown.tools) == tool_sync.MAX_PER_APP
