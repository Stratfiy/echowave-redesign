"""The chat offers its next question, instead of making you compose it.

The home screen has had opener cards since it was built. The thread --
where somebody has just read an answer and is most likely to want to keep
going -- had none, so every follow-up meant typing. This is the same
cards, in the place they matter.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.routes import agent_timeline


def _user(org: int | None = 7):
    return MagicMock(id=1, selected_organization_id=org)


class TestTheChipsComeFromThisAccount:
    @pytest.mark.asyncio
    async def test_it_returns_the_accounts_own_cards(self):
        cards = [
            {"kind": "asked_before", "text": "check my email"},
            {"kind": "busiest_bot", "text": "How did Clinic front desk do this week?"},
        ]
        with (
            patch("api.routes.team._members", AsyncMock(return_value=[])),
            patch.object(
                agent_timeline.db_client,
                "unreturned_missed_call_count",
                AsyncMock(return_value=0),
            ),
            patch.object(
                agent_timeline.home_openers, "gather", AsyncMock(return_value=cards)
            ),
        ):
            out = await agent_timeline.thread_chips(user=_user())
        assert [c.text for c in out.chips] == [
            "check my email",
            "How did Clinic front desk do this week?",
        ]
        assert out.chips[0].kind == "asked_before"

    @pytest.mark.asyncio
    async def test_a_card_with_no_text_is_never_offered(self):
        # An empty chip is a button that does nothing, which is worse than
        # one fewer chip.
        with (
            patch("api.routes.team._members", AsyncMock(return_value=[])),
            patch.object(
                agent_timeline.db_client,
                "unreturned_missed_call_count",
                AsyncMock(return_value=0),
            ),
            patch.object(
                agent_timeline.home_openers,
                "gather",
                AsyncMock(return_value=[{"kind": "x", "text": ""}, {"kind": "y"}]),
            ),
        ):
            out = await agent_timeline.thread_chips(user=_user())
        assert out.chips == []


class TestItNeverTakesTheThreadDown:
    @pytest.mark.asyncio
    async def test_a_failure_returns_no_chips_rather_than_an_error(self):
        # A screen with no chips is exactly as useful as it was yesterday.
        # A screen that will not load because the chips broke is worse than
        # the feature not existing.
        with patch(
            "api.routes.team._members",
            AsyncMock(side_effect=RuntimeError("database is having a day")),
        ):
            out = await agent_timeline.thread_chips(user=_user())
        assert out.chips == []

    @pytest.mark.asyncio
    async def test_no_organization_is_still_refused(self):
        # Tenancy is not something to be lenient about, unlike the chips.
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as caught:
            await agent_timeline.thread_chips(user=_user(org=None))
        assert caught.value.status_code == 400
