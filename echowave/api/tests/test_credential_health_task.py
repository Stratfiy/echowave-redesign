"""The alert an operator actually sees.

A rejected platform key is an outage with no customer to attribute it to and
no request to attach it to: it is found by a background sweep, and unless that
sweep says something, the first report is a caller hearing silence. These cover
what it says.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api.enums import PostHogEvent
from api.services.configuration.credential_validation import CredentialCheck
from api.tasks import credential_health


async def _run(checks):
    with (
        patch.object(
            credential_health,
            "validate_stored_credentials",
            new=AsyncMock(return_value=checks),
        ),
        patch.object(credential_health.db_client, "async_session"),
        patch.object(credential_health, "capture_event") as capture,
        patch.object(credential_health, "flush_posthog") as flush,
    ):
        await credential_health.check_platform_credentials(None)
    return capture, flush


class TestWhatItReports:
    @pytest.mark.asyncio
    async def test_a_new_rejection_is_reported(self):
        capture, _ = await _run([CredentialCheck("llm", "openai", False, changed=True)])
        capture.assert_called_once()
        assert capture.call_args.kwargs["event"] == PostHogEvent.PLATFORM_KEY_REJECTED
        assert capture.call_args.kwargs["properties"] == {
            "component": "llm",
            "provider": "openai",
        }

    @pytest.mark.asyncio
    async def test_a_recovery_is_reported(self):
        capture, _ = await _run([CredentialCheck("llm", "openai", True, changed=True)])
        assert capture.call_args.kwargs["event"] == PostHogEvent.PLATFORM_KEY_RECOVERED

    @pytest.mark.asyncio
    async def test_a_steady_state_reports_nothing(self):
        """Hourly. Twenty-four identical rows a day is not an alert."""
        capture, flush = await _run(
            [
                CredentialCheck("llm", "openai", False, changed=False),
                CredentialCheck("tts", "sarvam", True, changed=False),
            ]
        )
        capture.assert_not_called()
        flush.assert_not_called()

    @pytest.mark.asyncio
    async def test_it_flushes_so_the_alert_leaves_the_worker(self):
        """A queued event on a background job may sit until something else fires."""
        _, flush = await _run([CredentialCheck("llm", "openai", False, changed=True)])
        flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_credential_fragment_is_sent(self):
        """Analytics is not the place for any part of a key, last four included."""
        capture, _ = await _run([CredentialCheck("llm", "openai", False, changed=True)])
        assert set(capture.call_args.kwargs["properties"]) == {"component", "provider"}

    @pytest.mark.asyncio
    async def test_every_transition_gets_its_own_event(self):
        capture, _ = await _run(
            [
                CredentialCheck("llm", "openai", False, changed=True),
                CredentialCheck("tts", "sarvam", True, changed=True),
                CredentialCheck("stt", "deepgram", None, changed=False),
            ]
        )
        assert capture.call_count == 2


class TestItNeverBringsDownTheWorker:
    @pytest.mark.asyncio
    async def test_a_failing_sweep_is_logged_not_raised(self):
        with (
            patch.object(
                credential_health,
                "validate_stored_credentials",
                new=AsyncMock(side_effect=RuntimeError("database is on fire")),
            ),
            patch.object(credential_health.db_client, "async_session"),
        ):
            await credential_health.check_platform_credentials(None)
