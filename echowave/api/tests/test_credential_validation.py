"""Holding a key and holding a working key are different facts.

A revoked platform key stayed ``is_active``, so ``managed_availability`` went
on offering the tier it backed, an agent saved against it happily, and the
first to find out were inbound callers listening to silence. These cover the
check that closes that, and — mostly — the rule that keeps the check from
becoming a worse outage than the bug.

That rule is the NULL rule. ``key_validation`` reports three outcomes, not two:
a vendor rejecting a key is not the same as our being unable to ask, and only
the first may ever be recorded or acted on. Treating "could not ask" as
"broken" would withdraw every managed tier the moment a vendor had a bad
minute. Most of what is below exists to hold that line.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from api.db.models import PlatformProviderCredentialModel
from api.services.configuration import credential_validation
from api.services.configuration.credential_validation import (
    is_known_bad,
    validate_stored_credentials,
)
from api.services.configuration.key_validation import ValidationResult


def _row(provider="openai", component="llm", **kwargs):
    row = PlatformProviderCredentialModel(
        component=component,
        provider=provider,
        encrypted_key="ciphertext",
        key_last_four="abcd",
        is_active=True,
    )
    for field, value in kwargs.items():
        setattr(row, field, value)
    return row


class TestIsKnownBad:
    """The single place the three-state rule is enforced."""

    def test_a_rejected_key_is_known_bad(self):
        assert is_known_bad(_row(last_check_ok=False)) is True

    def test_an_accepted_key_is_not(self):
        assert is_known_bad(_row(last_check_ok=True)) is False

    def test_a_never_checked_key_is_not(self):
        """The most important row in this file.

        Every credential is NULL the moment the column ships. If that read as
        broken, deploying this feature would take every managed tier offline.
        """
        assert is_known_bad(_row(last_check_ok=None)) is False

    def test_a_missing_credential_is_not(self):
        assert is_known_bad(None) is False


class _Session:
    """Enough AsyncSession to run the sweep against in-memory rows."""

    def __init__(self, rows):
        self._rows = rows
        self.committed = False

    async def scalars(self, _query):
        rows = self._rows

        class _Result:
            def all(self_inner):
                return rows

        return _Result()

    async def commit(self):
        self.committed = True


async def _sweep(rows, *, outcome, key="sk-live", message=None):
    session = _Session(rows)
    with (
        patch.object(
            credential_validation.platform_credentials,
            "resolve_api_key",
            new=AsyncMock(return_value=key),
        ),
        patch.object(
            credential_validation.key_validation,
            "validate_key",
            new=AsyncMock(return_value=ValidationResult(outcome, message)),
        ),
    ):
        results = await validate_stored_credentials(session)
    return session, results


class TestWhatTheSweepRecords:
    @pytest.mark.asyncio
    async def test_a_rejection_is_recorded_with_the_vendors_words(self):
        row = _row()
        _, results = await _sweep(
            [row], outcome="invalid", message="OpenAI rejected the key (401)"
        )
        assert row.last_check_ok is False
        assert row.last_checked_at is not None
        assert "401" in row.last_check_error
        assert (results[0].ok, results[0].changed) == (False, True)

    @pytest.mark.asyncio
    async def test_an_acceptance_clears_a_previous_error(self):
        """Replacing a bad key has to visibly fix the row, not just add to it."""
        row = _row(last_check_ok=False, last_check_error="OpenAI rejected the key")
        await _sweep([row], outcome="valid")
        assert row.last_check_ok is True
        assert row.last_check_error is None

    @pytest.mark.asyncio
    async def test_unverified_leaves_a_known_good_verdict_alone(self):
        """A vendor having a bad minute must not erase what we already knew."""
        checked_at = datetime(2026, 9, 1, tzinfo=UTC)
        row = _row(last_check_ok=True, last_checked_at=checked_at)
        _, results = await _sweep([row], outcome="unverified", message="timeout")
        assert row.last_check_ok is True
        assert row.last_checked_at == checked_at
        assert (results[0].ok, results[0].changed) == (None, False)

    @pytest.mark.asyncio
    async def test_unverified_leaves_a_known_bad_verdict_alone(self):
        """Someone may be acting on that verdict. Do not quietly withdraw it."""
        row = _row(last_check_ok=False, last_check_error="rejected")
        await _sweep([row], outcome="unverified")
        assert row.last_check_ok is False
        assert row.last_check_error == "rejected"

    @pytest.mark.asyncio
    async def test_a_key_that_will_not_decrypt_is_a_definite_failure(self):
        """No vendor will ever report this one, so we record it ourselves."""
        row = _row()
        _, results = await _sweep([row], outcome="valid", key=None)
        assert row.last_check_ok is False
        assert "PLATFORM_CREDENTIAL_SECRET" in row.last_check_error
        assert (results[0].ok, results[0].changed) == (False, True)

    @pytest.mark.asyncio
    async def test_the_error_is_truncated(self):
        row = _row()
        await _sweep([row], outcome="invalid", message="x" * 5000)
        assert len(row.last_check_error) <= credential_validation.MAX_ERROR_LENGTH

    @pytest.mark.asyncio
    async def test_the_sweep_commits(self):
        session, _ = await _sweep([_row()], outcome="valid")
        assert session.committed is True

    @pytest.mark.asyncio
    async def test_an_empty_vault_is_not_an_error(self):
        session, results = await _sweep([], outcome="valid")
        assert results == []


class TestTransitions:
    """What an alert fires on.

    The sweep runs hourly. Alerting on state rather than on change would turn a
    one-day outage into twenty-four identical events and bury the one that says
    when it started, so only a verdict that actually moved is marked changed.
    """

    @pytest.mark.asyncio
    async def test_a_first_rejection_is_a_change(self):
        _, results = await _sweep([_row()], outcome="invalid", message="401")
        assert results[0].changed is True

    @pytest.mark.asyncio
    async def test_a_key_still_rejected_is_not_a_change(self):
        """The hour-two event nobody needs."""
        row = _row(last_check_ok=False)
        _, results = await _sweep([row], outcome="invalid", message="401")
        assert results[0].ok is False
        assert results[0].changed is False

    @pytest.mark.asyncio
    async def test_recovery_is_a_change(self):
        row = _row(last_check_ok=False)
        _, results = await _sweep([row], outcome="valid")
        assert (results[0].ok, results[0].changed) == (True, True)

    @pytest.mark.asyncio
    async def test_a_key_still_good_is_not_a_change(self):
        row = _row(last_check_ok=True)
        _, results = await _sweep([row], outcome="valid")
        assert results[0].changed is False

    @pytest.mark.asyncio
    async def test_the_first_ever_pass_is_a_change(self):
        """NULL to True is news: it is the first time we ever confirmed it."""
        row = _row(last_check_ok=None)
        _, results = await _sweep([row], outcome="valid")
        assert results[0].changed is True

    @pytest.mark.asyncio
    async def test_being_unable_to_ask_is_never_a_change(self):
        """Nothing moved, because nothing was learned."""
        for previous in (None, True, False):
            row = _row(last_check_ok=previous)
            _, results = await _sweep([row], outcome="unverified")
            assert results[0].changed is False
            assert row.last_check_ok is previous


class TestTheRecheckEndpointCanCountTheResult:
    """The sweep and its caller must agree on what comes back.

    They did not. ``validate_stored_credentials`` once returned tuples, and the
    ``/recheck`` endpoint counted them by unpacking three-wide. When the tuples
    became :class:`CredentialCheck` records, that unpacking became a TypeError
    on a non-iterable — so the button an operator presses after replacing a key
    stopped answering at all, and nothing here noticed because nothing here
    called it.
    """

    @pytest.mark.asyncio
    async def test_it_reports_rejected_and_unverified_counts(self):
        from api.routes import platform_credentials as route
        from api.services.configuration.credential_validation import CredentialCheck

        checks = [
            CredentialCheck("llm", "openai", True),
            CredentialCheck("tts", "elevenlabs", False),
            CredentialCheck("stt", "deepgram", None),
        ]

        class _Session:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *exc):
                return False

        with (
            patch.object(route.db_client, "async_session", _Session),
            patch.object(
                route.credential_validation,
                "validate_stored_credentials",
                AsyncMock(return_value=checks),
            ),
            patch.object(route.creds, "list_credentials", AsyncMock(return_value=[])),
        ):
            body = await route.recheck_provider_keys()

        assert body["checked"] == 3
        assert body["rejected"] == 1
        # The vendor we could not ask is reported as exactly that, and never
        # folded into the rejections.
        assert body["unverified"] == 1
