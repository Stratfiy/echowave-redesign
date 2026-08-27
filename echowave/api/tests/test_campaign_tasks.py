"""
Tests for api.tasks.campaign_tasks failure handling.

Specifically: each kind of failure that pauses or fails a campaign should
write a specific, identifiable entry into the campaign log so operators
can tell at a glance why a campaign stopped.
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.services.campaign.errors import (
    ConcurrentSlotAcquisitionError,
    PhoneNumberPoolExhaustedError,
)
from api.tasks.campaign_tasks import (
    CONSECUTIVE_BATCH_FAILURE_COUNTER_KEY,
    MAX_CONSECUTIVE_BATCH_FAILURES,
    process_campaign_batch,
)


class TestProcessCampaignBatchFailureLogs:
    """``process_campaign_batch`` should log a *specific* event for each
    distinct failure mode, not collapse them all into a generic
    ``batch_failed`` entry."""

    @pytest.mark.asyncio
    async def test_phone_number_pool_exhausted_retries_before_final_failure(self):
        """The first two consecutive pool exhaustion attempts keep the
        campaign running and schedule another batch."""
        with (
            patch("api.tasks.campaign_tasks.campaign_call_dispatcher") as mock_disp,
            patch("api.tasks.campaign_tasks.db_client") as mock_db,
            patch(
                "api.tasks.campaign_tasks.get_campaign_event_publisher"
            ) as mock_get_pub,
        ):
            mock_disp.process_batch = AsyncMock(
                side_effect=PhoneNumberPoolExhaustedError(organization_id=7)
            )
            mock_db.increment_campaign_metadata_counter = AsyncMock(return_value=2)
            mock_db.update_campaign = AsyncMock()
            mock_db.append_campaign_log = AsyncMock()
            mock_pub = AsyncMock()
            mock_get_pub.return_value = mock_pub

            await process_campaign_batch({}, campaign_id=42)

            mock_db.update_campaign.assert_not_awaited()
            mock_pub.publish_batch_failed.assert_not_awaited()
            mock_pub.publish_batch_completed.assert_awaited_once_with(
                campaign_id=42,
                processed_count=0,
                failed_count=0,
                batch_size=10,
            )

            mock_db.append_campaign_log.assert_called_once()
            kwargs = mock_db.append_campaign_log.call_args.kwargs
            assert kwargs["campaign_id"] == 42
            assert kwargs["event"] == "phone_number_pool_exhausted_retry"
            assert kwargs["level"] == "warning"
            assert kwargs["details"]["organization_id"] == 7
            assert kwargs["details"]["attempt"] == 2

    @pytest.mark.asyncio
    async def test_phone_number_pool_exhausted_fails_on_third_attempt(self):
        """The third consecutive pool exhaustion attempt marks the campaign
        failed with a specific operator-facing log entry."""
        with (
            patch("api.tasks.campaign_tasks.campaign_call_dispatcher") as mock_disp,
            patch("api.tasks.campaign_tasks.db_client") as mock_db,
            patch(
                "api.tasks.campaign_tasks.get_campaign_event_publisher"
            ) as mock_get_pub,
        ):
            mock_disp.process_batch = AsyncMock(
                side_effect=PhoneNumberPoolExhaustedError(organization_id=7)
            )
            mock_db.increment_campaign_metadata_counter = AsyncMock(return_value=3)
            mock_db.update_campaign = AsyncMock()
            mock_db.append_campaign_log = AsyncMock()
            mock_pub = AsyncMock()
            mock_get_pub.return_value = mock_pub

            with pytest.raises(PhoneNumberPoolExhaustedError):
                await process_campaign_batch({}, campaign_id=42)

            mock_db.update_campaign.assert_called_once_with(
                campaign_id=42, state="failed"
            )
            mock_pub.publish_batch_failed.assert_awaited_once()

            mock_db.append_campaign_log.assert_called_once()
            kwargs = mock_db.append_campaign_log.call_args.kwargs
            assert kwargs["campaign_id"] == 42
            assert kwargs["event"] == "phone_number_pool_exhausted"
            assert kwargs["level"] == "error"
            assert "phone number" in kwargs["message"].lower()
            assert kwargs["details"]["organization_id"] == 7
            assert kwargs["details"]["attempt"] == 3

    @pytest.mark.asyncio
    async def test_concurrent_slot_timeout_still_logs_specific_event(self):
        """Regression guard: the existing ConcurrentSlotAcquisitionError branch
        should keep logging its specific reason."""
        with (
            patch("api.tasks.campaign_tasks.campaign_call_dispatcher") as mock_disp,
            patch("api.tasks.campaign_tasks.db_client") as mock_db,
            patch(
                "api.tasks.campaign_tasks.get_campaign_event_publisher"
            ) as mock_get_pub,
        ):
            mock_disp.process_batch = AsyncMock(
                side_effect=ConcurrentSlotAcquisitionError(
                    organization_id=7, campaign_id=42, wait_time=30.0
                )
            )
            mock_db.update_campaign = AsyncMock()
            mock_db.append_campaign_log = AsyncMock()
            mock_pub = AsyncMock()
            mock_get_pub.return_value = mock_pub

            with pytest.raises(ConcurrentSlotAcquisitionError):
                await process_campaign_batch({}, campaign_id=42)

            mock_db.append_campaign_log.assert_called_once()
            kwargs = mock_db.append_campaign_log.call_args.kwargs
            assert kwargs["event"] == "batch_failed"
            assert kwargs["details"]["reason"] == "concurrent_slot_timeout"


class TestAnUnexpectedBatchFailureDoesNotKillTheCampaign:
    """One bad batch is ten calls; a campaign is thousands.

    Failing the whole thing on the first unexpected error throws away every row
    not yet dialled and needs a human to notice and restart it — for a carrier
    502 or a database reconnect that the next batch would not have hit. The
    budget below is the same one pool exhaustion already gets, so a campaign
    that is genuinely broken still dies on the third attempt.
    """

    @pytest.mark.asyncio
    async def test_an_early_failure_keeps_the_campaign_running(self):
        with (
            patch("api.tasks.campaign_tasks.campaign_call_dispatcher") as mock_disp,
            patch("api.tasks.campaign_tasks.db_client") as mock_db,
            patch(
                "api.tasks.campaign_tasks.get_campaign_event_publisher"
            ) as mock_get_pub,
        ):
            mock_disp.process_batch = AsyncMock(side_effect=RuntimeError("carrier 502"))
            mock_db.increment_campaign_metadata_counter = AsyncMock(return_value=1)
            mock_db.update_campaign = AsyncMock()
            mock_db.append_campaign_log = AsyncMock()
            mock_pub = AsyncMock()
            mock_get_pub.return_value = mock_pub

            await process_campaign_batch({}, campaign_id=42)

            mock_db.update_campaign.assert_not_awaited()
            # batch_failed is what fails the campaign through the orchestrator,
            # so publishing it here would defeat the budget entirely.
            mock_pub.publish_batch_failed.assert_not_awaited()
            mock_pub.publish_batch_completed.assert_awaited_once_with(
                campaign_id=42,
                processed_count=0,
                failed_count=0,
                batch_size=10,
            )

            kwargs = mock_db.append_campaign_log.call_args.kwargs
            assert kwargs["event"] == "batch_failed_retry"
            assert kwargs["level"] == "warning"

    @pytest.mark.asyncio
    async def test_the_budget_runs_out_and_the_campaign_fails(self):
        """Still fails — later, not never."""
        with (
            patch("api.tasks.campaign_tasks.campaign_call_dispatcher") as mock_disp,
            patch("api.tasks.campaign_tasks.db_client") as mock_db,
            patch(
                "api.tasks.campaign_tasks.get_campaign_event_publisher"
            ) as mock_get_pub,
        ):
            mock_disp.process_batch = AsyncMock(
                side_effect=RuntimeError("still broken")
            )
            mock_db.increment_campaign_metadata_counter = AsyncMock(
                return_value=MAX_CONSECUTIVE_BATCH_FAILURES
            )
            mock_db.update_campaign = AsyncMock()
            mock_db.append_campaign_log = AsyncMock()
            mock_pub = AsyncMock()
            mock_get_pub.return_value = mock_pub

            with pytest.raises(RuntimeError):
                await process_campaign_batch({}, campaign_id=42)

            mock_db.update_campaign.assert_awaited_once_with(
                campaign_id=42, state="failed"
            )
            mock_pub.publish_batch_failed.assert_awaited_once()
            kwargs = mock_db.append_campaign_log.call_args.kwargs
            assert kwargs["event"] == "batch_failed"
            assert kwargs["level"] == "error"

    @pytest.mark.asyncio
    async def test_a_batch_that_completes_clears_the_budget(self):
        """Consecutive, not cumulative.

        Without the reset the counter is a lifetime total, and a campaign
        running for hours is eventually killed by three unrelated blips that
        were never in a row.
        """
        with (
            patch("api.tasks.campaign_tasks.campaign_call_dispatcher") as mock_disp,
            patch("api.tasks.campaign_tasks.db_client") as mock_db,
            patch(
                "api.tasks.campaign_tasks.get_campaign_event_publisher"
            ) as mock_get_pub,
        ):
            mock_disp.process_batch = AsyncMock(return_value=10)
            mock_db.reset_campaign_metadata_counter = AsyncMock()
            mock_db.append_campaign_log = AsyncMock()
            mock_get_pub.return_value = AsyncMock()

            await process_campaign_batch({}, campaign_id=42)

            reset_keys = {
                call.kwargs["key"]
                for call in mock_db.reset_campaign_metadata_counter.call_args_list
            }
            assert CONSECUTIVE_BATCH_FAILURE_COUNTER_KEY in reset_keys
