"""Decibyl offers an app as a card, and never as a sentence about a screen.

The defect this closes: asked to read a Gmail nobody had connected, Decibyl
answered "connect it via Marketplace -> Tools", which is a correct sentence
that ends the conversation. The tests here hold the two halves of the fix --
that the card carries what the screen would have shown, and that every
refusal tells the model *why*, so it says something true rather than
offering the same app again.
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.enums import AgentEventKind
from api.services.integrations.composio.catalogue import (
    SETUP_ONE_CLICK,
    SETUP_OURS,
    Connector,
)
from api.services.workflow import connector_offer, decibyl


def _connector(slug="gmail", name="Gmail", setup=SETUP_ONE_CLICK, **kwargs):
    row = {
        "slug": slug,
        "name": name,
        "description": f"{name} does things",
        "logo": f"https://logos.composio.dev/api/{slug}",
        "group": "Email",
        "setup": setup,
        "tools_count": 9,
    }
    row.update(kwargs)
    return Connector(**row)


def _offering(rows, connected=()):
    """Patch the two readings ``offer`` makes: the catalogue and this
    account's connected apps."""
    return (
        patch.object(connector_offer, "is_configured", return_value=True),
        patch.object(
            connector_offer.catalogue, "connectors", AsyncMock(return_value=rows)
        ),
        patch.object(
            connector_offer,
            "connected_toolkits",
            AsyncMock(return_value=list(connected)),
        ),
    )


class TestTheCard:
    @pytest.mark.asyncio
    async def test_it_writes_a_card_with_what_the_app_brings(self):
        rows = [_connector()]
        a, b, c = _offering(rows)
        with (
            a,
            b,
            c,
            patch.object(
                connector_offer.agent_timeline, "record", AsyncMock()
            ) as record,
        ):
            result = await connector_offer.offer(
                organization_id=1,
                arguments={"app": "gmail", "why": "to mail your staff"},
            )
        assert result["status"] == "offered"
        payload = record.await_args.kwargs["payload"]
        assert (
            record.await_args.kwargs["kind"] == AgentEventKind.CONNECTOR_OFFERED.value
        )
        assert payload["app"] == "gmail"
        assert payload["name"] == "Gmail"
        assert payload["tools_count"] == 9
        assert payload["logo"]
        assert payload["why"] == "to mail your staff"

    @pytest.mark.asyncio
    async def test_the_model_is_told_it_is_not_connected_yet(self):
        """The sentence that stops "Gmail is connected now" -- a model that
        called a tool and got a success reads it as done unless told."""
        a, b, c = _offering([_connector()])
        with (
            a,
            b,
            c,
            patch.object(connector_offer.agent_timeline, "record", AsyncMock()),
        ):
            result = await connector_offer.offer(
                organization_id=1, arguments={"app": "gmail"}
            )
        assert "NOT connected yet" in result["note"]

    @pytest.mark.asyncio
    async def test_a_name_finds_the_app_a_person_would_say(self):
        rows = [_connector(slug="googlecalendar", name="Google Calendar")]
        a, b, c = _offering(rows)
        with (
            a,
            b,
            c,
            patch.object(
                connector_offer.agent_timeline, "record", AsyncMock()
            ) as record,
        ):
            await connector_offer.offer(
                organization_id=1, arguments={"app": "google calendar"}
            )
        assert record.await_args.kwargs["payload"]["app"] == "googlecalendar"

    @pytest.mark.asyncio
    async def test_the_slug_wins_over_a_loose_name_match(self):
        rows = [
            _connector(slug="gmailbot", name="Gmail Helper"),
            _connector(slug="gmail", name="Gmail"),
        ]
        a, b, c = _offering(rows)
        with (
            a,
            b,
            c,
            patch.object(
                connector_offer.agent_timeline, "record", AsyncMock()
            ) as record,
        ):
            await connector_offer.offer(organization_id=1, arguments={"app": "gmail"})
        assert record.await_args.kwargs["payload"]["app"] == "gmail"


class TestEveryRefusalSaysWhy:
    @pytest.mark.asyncio
    async def test_an_app_already_connected_is_not_offered_again(self):
        a, b, c = _offering([_connector()], connected=["GMAIL"])
        with (
            a,
            b,
            c,
            patch.object(
                connector_offer.agent_timeline, "record", AsyncMock()
            ) as record,
        ):
            result = await connector_offer.offer(
                organization_id=1, arguments={"app": "gmail"}
            )
        assert result["status"] == "already_connected"
        assert "already connected" in result["reason"]
        record.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_app_we_integrate_ourselves_points_at_its_own_screen(self):
        rows = [
            _connector(
                slug="twilio",
                name="Twilio",
                setup=SETUP_OURS,
                setup_url="/numbers",
                also_connectable=False,
            )
        ]
        a, b, c = _offering(rows)
        with (
            a,
            b,
            c,
            patch.object(
                connector_offer.agent_timeline, "record", AsyncMock()
            ) as record,
        ):
            result = await connector_offer.offer(
                organization_id=1, arguments={"app": "twilio"}
            )
        assert result["status"] == "not_offered"
        assert "/numbers" in result["reason"]
        record.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_app_that_does_not_exist_is_named(self):
        a, b, c = _offering([_connector()])
        with (
            a,
            b,
            c,
            patch.object(connector_offer.agent_timeline, "record", AsyncMock()),
        ):
            result = await connector_offer.offer(
                organization_id=1, arguments={"app": "nosuchapp"}
            )
        assert result["status"] == "not_offered"
        assert "nosuchapp" in result["reason"]

    @pytest.mark.asyncio
    async def test_a_platform_without_connectors_says_so(self):
        with patch.object(connector_offer, "is_configured", return_value=False):
            result = await connector_offer.offer(
                organization_id=1, arguments={"app": "gmail"}
            )
        assert result["status"] == "not_offered"
        assert "not switched on" in result["reason"]

    @pytest.mark.asyncio
    async def test_an_unreadable_catalogue_is_not_a_missing_app(self):
        """Composio down must not produce "there is no app called gmail",
        which would send the person to fix a problem they do not have."""
        a, b, c = _offering([])
        with a, b, c:
            result = await connector_offer.offer(
                organization_id=1, arguments={"app": "gmail"}
            )
        assert "could not be read" in result["reason"]


class TestItIsReachable:
    def test_decibyl_offers_the_tool(self):
        assert connector_offer.TOOL_NAME in {t["name"] for t in decibyl.office_tools()}

    def test_decibyl_thread_shows_what_it_writes(self):
        """The allowlist in thread_filter is the silent-absence shape: a card
        whose kind is missing is written, stored and never seen."""
        assert (
            AgentEventKind.CONNECTOR_OFFERED.value in decibyl.thread_filter()["kinds"]
        )

    def test_the_context_line_points_at_the_card_not_a_screen(self):
        from api.services.workflow import connected_tools

        block = connected_tools.apps_block([])
        assert connector_offer.TOOL_NAME in block
        assert "Marketplace" in block and "Never tell anybody" in block
