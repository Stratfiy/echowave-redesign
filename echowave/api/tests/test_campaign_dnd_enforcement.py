"""The campaign path's do-not-disturb gate — the wiring, not the gate itself.

``test_dnd_gate.py`` covers ``compliance/dnd.py`` thoroughly: normalisation,
the calling window, failing closed, tenant isolation. What nothing covered was
whether the campaign dispatcher **calls** it.

That distinction is the whole reason this file exists. Delete the
``assert_may_call`` block from ``dispatch_call`` and every one of those gate
tests still passes, the dispatcher's own ten tests still pass, and the product
quietly starts dialling numbers that asked not to be dialled — at forty
thousand rows a campaign, which is the one path where volume turns a mistake
into a regulatory incident rather than a complaint.

So these tests assert the seam. Four properties, each of which is a different
way for the gate to be present and useless:

* it is called at all, per row, with the organization's timezone;
* a refusal stops the dial rather than merely logging;
* a refusal is **terminal** — it must not feed the retry path, or the row is
  re-dialled and the breach is the retry;
* a refusal must not trip the **circuit breaker**, which exists to detect a
  carrier going bad. A campaign against a well-scrubbed list would otherwise
  look like a carrier outage and halt itself.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.campaign import campaign_call_dispatcher as dispatcher_module
from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher
from api.services.compliance import dnd


def _queued_run(phone_number="+919876543210"):
    return SimpleNamespace(
        id=4242,
        context_variables={"phone_number": phone_number},
        workflow_id=7,
        retry_count=0,
    )


def _campaign():
    return SimpleNamespace(
        id=99,
        organization_id=1,
        workflow_id=7,
        telephony_configuration_id=3,
    )


async def _run_dispatch(refusal: Exception | None):
    """Drive ``dispatch_call`` far enough to observe the gate.

    Everything before the gate is stubbed to succeed and everything after it
    is stubbed to explode, so a call that reaches the carrier is unmistakable
    rather than merely unasserted.
    """
    d = CampaignCallDispatcher()
    d.mark_queued_run_refused = AsyncMock()
    d.get_provider_for_campaign = AsyncMock(
        side_effect=AssertionError("dialled a number the gate should have refused")
    )

    assert_may_call = AsyncMock(
        side_effect=refusal if refusal else None,
        return_value="+919876543210",
    )

    with (
        patch.object(
            dispatcher_module.db_client,
            "get_workflow_by_id",
            AsyncMock(return_value=MagicMock()),
        ),
        patch.object(
            dispatcher_module.liveness, "assert_workflow_may_take_calls", MagicMock()
        ),
        patch.object(
            dispatcher_module,
            "get_organization_preferences",
            AsyncMock(return_value=SimpleNamespace(timezone="Asia/Kolkata")),
        ),
        patch.object(dispatcher_module.dnd, "assert_may_call", assert_may_call),
    ):
        await d.dispatch_call(_queued_run(), _campaign(), MagicMock())

    return d, assert_may_call


@pytest.mark.asyncio
async def test_the_gate_is_called_before_the_carrier_is():
    """The seam itself. If this fails, nothing else in this file matters."""
    _, assert_may_call = await _run_dispatch(
        dnd.DoNotDisturbListed("on the list")
    )

    assert_may_call.assert_awaited_once()
    org_id, number = assert_may_call.await_args.args
    assert org_id == 1
    assert number == "+919876543210"


@pytest.mark.asyncio
async def test_the_window_is_judged_in_the_organizations_timezone():
    """TCCCPR's 09:00–21:00 is in the *recipient's* local time, and the
    organization's configured zone is the proxy for it. Passing no zone would
    judge an Indian campaign's window in UTC — five and a half hours out, which
    silently permits calls until 02:30 IST."""
    _, assert_may_call = await _run_dispatch(dnd.OutsideCallingHours("closed"))

    assert assert_may_call.await_args.kwargs["timezone_name"] == "Asia/Kolkata"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refusal,expected_reason",
    [
        (dnd.DoNotDisturbListed("listed"), "dnd_listed"),
        (dnd.OutsideCallingHours("closed"), "outside_calling_hours"),
    ],
)
async def test_a_refusal_stops_the_dial_and_is_recorded_with_its_reason(
    refusal, expected_reason
):
    """Both refusal kinds end the row. The stable machine reason is what makes
    "why was this row never called" answerable without reading logs."""
    d, _ = await _run_dispatch(refusal)

    d.mark_queued_run_refused.assert_awaited_once()
    assert d.mark_queued_run_refused.await_args.kwargs["reason"] == expected_reason
    # get_provider_for_campaign raises if reached — reaching it is the dial.
    d.get_provider_for_campaign.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_refusal_closes_the_row_terminally_rather_than_requeueing_it():
    """Retrying is the breach, not the recovery.

    Asserted against the actual database write rather than a mocked handler:
    the row must end up ``failed`` and stamped ``processed_at``, which is what
    takes it out of the queue for good. A row left claimed-but-unprocessed is
    picked up by the next worker and dialled.
    """
    d = CampaignCallDispatcher()
    d.get_provider_for_campaign = AsyncMock(
        side_effect=AssertionError("dialled a number the gate should have refused")
    )
    update = AsyncMock()

    with (
        patch.object(
            dispatcher_module.db_client,
            "get_workflow_by_id",
            AsyncMock(return_value=MagicMock()),
        ),
        patch.object(
            dispatcher_module.liveness, "assert_workflow_may_take_calls", MagicMock()
        ),
        patch.object(
            dispatcher_module,
            "get_organization_preferences",
            AsyncMock(return_value=SimpleNamespace(timezone="Asia/Kolkata")),
        ),
        patch.object(
            dispatcher_module.dnd,
            "assert_may_call",
            AsyncMock(side_effect=dnd.DoNotDisturbListed("listed")),
        ),
        patch.object(dispatcher_module.db_client, "update_queued_run", update),
    ):
        await d.dispatch_call(_queued_run(), _campaign(), MagicMock())

    update.assert_awaited_once()
    written = update.await_args.kwargs
    assert written["state"] == "failed"
    assert written["refusal_reason"] == "dnd_listed"
    assert written["processed_at"] is not None


@pytest.mark.asyncio
async def test_a_refusal_does_not_trip_the_circuit_breaker():
    """The breaker watches for a carrier going bad. A well-scrubbed list
    produces many refusals and no carrier problem at all — counting them as
    failures would halt a campaign that is behaving exactly as intended."""
    breaker = MagicMock()
    breaker.record_failure = MagicMock()
    breaker.record_success = MagicMock()

    with patch.object(dispatcher_module, "circuit_breaker", breaker, create=True):
        await _run_dispatch(dnd.DoNotDisturbListed("listed"))

    breaker.record_failure.assert_not_called()


class TestTheGateCannotBeSilentlyDisabled:
    """`DND_ENFORCEMENT_ENABLED` short-circuits the whole gate. It is one
    environment variable between a compliant deployment and a non-compliant
    one, so its default is asserted rather than trusted."""

    def test_enforcement_is_on_unless_explicitly_switched_off(self):
        import api.constants as constants

        assert constants.DND_ENFORCEMENT_ENABLED is True

    def test_the_calling_window_defaults_to_the_regulated_hours(self):
        import api.constants as constants

        assert constants.CALLING_HOURS_START == "09:00"
        assert constants.CALLING_HOURS_END == "21:00"
