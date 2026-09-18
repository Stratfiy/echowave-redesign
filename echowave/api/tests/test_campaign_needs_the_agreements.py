"""A campaign is somebody else's phone ringing. The terms come first.

`Agreement.required` is documented as "whether a customer must accept it
before running a campaign", and nothing checked it there. The gate existed
only on number purchase, so an account that bought its number before the gate
existed, or brings its own Twilio or SIP trunk, could dial a list having
accepted nothing at all.

That is the exact case the acceptable use policy and the "consent is yours to
hold" clause exist to cover, and an unaccepted policy gives no basis to
suspend anyone -- which was the point of publishing it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.campaign.runner import CampaignRunnerService
from api.services.compliance import agreements


def _campaign(organization_id: int = 7, state: str = "created"):
    return SimpleNamespace(
        id=1,
        organization_id=organization_id,
        state=state,
        orchestrator_metadata={},
        total_rows=3,
        source_type="csv",
        source_id="s1",
    )


async def _start(outstanding: tuple[str, ...], *, campaign=None):
    """Run start_campaign with the acceptance check answering as told."""
    runner = CampaignRunnerService()
    raise_with = agreements.AgreementsOutstanding(outstanding) if outstanding else None

    async def _require(_session, *, organization_id):
        if raise_with:
            raise raise_with

    from api.services.campaign import runner as runner_module

    with (
        patch.object(
            runner_module.db_client,
            "get_campaign_by_id",
            AsyncMock(return_value=campaign or _campaign()),
        ),
        patch.object(runner_module.db_client, "update_campaign", AsyncMock()) as update,
        patch.object(
            runner_module.agreements,
            "require_accepted",
            AsyncMock(side_effect=_require),
        ) as check,
        patch.object(runner_module, "enqueue_job", AsyncMock()) as enqueue,
    ):
        error = None
        try:
            await runner.start_campaign(1)
        except agreements.AgreementsOutstanding as exc:
            error = exc
    return error, check, update, enqueue


@pytest.mark.asyncio
class TestTheGate:
    async def test_an_account_owing_the_terms_cannot_start_a_campaign(self):
        error, _check, update, enqueue = await _start(("terms",))

        assert error is not None
        assert error.keys == ("terms",)
        # Nothing moved: no state change, no work queued, no calls placed.
        update.assert_not_awaited()
        enqueue.assert_not_awaited()

    async def test_it_names_what_is_owed_so_the_screen_can_show_it(self):
        error, _check, _update, _enqueue = await _start(("terms", "dpa"))

        assert error is not None
        assert "Terms of Service" in str(error)
        assert "Data Processing Agreement" in str(error)

    async def test_an_account_that_has_accepted_is_not_stopped(self):
        error, check, update, _enqueue = await _start(())

        assert error is None
        assert check.await_args.kwargs["organization_id"] == 7
        # It got past the gate and moved the campaign on.
        assert update.await_count >= 1

    async def test_the_check_runs_before_the_campaign_is_touched(self):
        """Refusing after the state has moved leaves a campaign stuck in
        'syncing' that nobody asked to start."""
        _error, check, update, _enqueue = await _start(("dpa",))

        assert check.await_count == 1
        update.assert_not_awaited()

    async def test_a_campaign_in_the_wrong_state_still_fails_on_the_state(self):
        """The gate is added to the front of start_campaign, not in place of
        what was already there."""
        runner = CampaignRunnerService()
        from api.services.campaign import runner as runner_module

        with (
            patch.object(
                runner_module.db_client,
                "get_campaign_by_id",
                AsyncMock(return_value=_campaign(state="running")),
            ),
            patch.object(runner_module.agreements, "require_accepted", AsyncMock()),
        ):
            with pytest.raises(ValueError, match="created"):
                await runner.start_campaign(1)
