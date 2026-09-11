"""A rejected realtime key must not shadow a working one.

This happened. The vault held a dead ``llm/openai_realtime`` row beside a
healthy ``llm/openai`` one. An exact row won unconditionally, so every managed
realtime call authenticated with the dead key, ``is_known_bad`` saw a rejected
credential and withdrew the realtime tier from the model picker — while a
working key for the same vendor account sat one row away the entire time. One
stale row took the tier down, and the only cure was noticing and deleting it.

The realtime fallback already existed for the *absent* case, on the reasoning
that a speech-to-speech provider is the same vendor account as its ordinary
sibling. The same reasoning covers the unusable case, and it did not.
"""

from unittest.mock import patch

import pytest

from api.services.configuration import platform_credentials as creds


class _Row:
    """Just enough of PlatformProviderCredentialModel for the decision."""

    def __init__(self, provider: str, last_check_ok):
        self.provider = provider
        self.last_check_ok = last_check_ok
        self.encrypted_key = f"cipher-for-{provider}"


def _vault(**rows):
    """Stub _active_row over a {provider: row-or-None} table."""

    async def lookup(session, component, provider):
        return rows.get(provider)

    return patch.object(creds, "_active_row", lookup)


@pytest.mark.asyncio
class TestWhichRowAuthenticates:
    async def test_a_rejected_realtime_key_steps_aside_for_a_healthy_sibling(self):
        """The bug. Before this, the dead row won and the tier went dark."""
        with _vault(
            openai_realtime=_Row("openai_realtime", False),
            openai=_Row("openai", True),
        ):
            row = await creds._effective_row(None, "llm", "openai_realtime")
        assert row.provider == "openai"

    async def test_an_absent_realtime_key_still_borrows_the_sibling(self):
        """The behaviour that already existed, kept."""
        with _vault(openai=_Row("openai", True)):
            row = await creds._effective_row(None, "llm", "openai_realtime")
        assert row.provider == "openai"

    async def test_a_working_realtime_key_is_left_alone(self):
        """A deployment that deliberately stores a separate realtime key keeps
        using it."""
        with _vault(
            openai_realtime=_Row("openai_realtime", True),
            openai=_Row("openai", True),
        ):
            row = await creds._effective_row(None, "llm", "openai_realtime")
        assert row.provider == "openai_realtime"

    async def test_an_unproven_realtime_key_is_left_alone(self):
        """Only *known* bad steps aside. A probe that timed out, or a key never
        checked, must never silently reroute a call onto a different key — the
        NULL rule credential_validation.is_known_bad states."""
        with _vault(
            openai_realtime=_Row("openai_realtime", None),
            openai=_Row("openai", True),
        ):
            row = await creds._effective_row(None, "llm", "openai_realtime")
        assert row.provider == "openai_realtime"

    async def test_it_does_not_swap_a_bad_key_for_another_bad_key(self):
        """Nothing is gained, and the caller's own error path should report the
        real problem rather than a different vendor's."""
        with _vault(
            openai_realtime=_Row("openai_realtime", False),
            openai=_Row("openai", False),
        ):
            row = await creds._effective_row(None, "llm", "openai_realtime")
        assert row.provider == "openai_realtime"

    async def test_a_rejected_ordinary_key_has_nowhere_to_fall_back_to(self):
        """This is only about realtime siblings. An ordinary provider with a
        rejected key keeps it, and the tier is withdrawn as before."""
        with _vault(openai=_Row("openai", False)):
            row = await creds._effective_row(None, "llm", "openai")
        assert row.provider == "openai"

    async def test_ultravox_maps_to_itself_and_never_borrows(self):
        """It has no ordinary sibling; realtime_key_provider maps it to itself,
        so the fallback must not fire even when its key is rejected."""
        with _vault(ultravox_realtime=_Row("ultravox_realtime", False)):
            row = await creds._effective_row(None, "llm", "ultravox_realtime")
        assert row.provider == "ultravox_realtime"

    async def test_nothing_stored_at_all_is_still_nothing(self):
        with _vault():
            assert await creds._effective_row(None, "llm", "openai_realtime") is None


@pytest.mark.asyncio
class TestTheTwoCallersCannotDisagree:
    """The half-a-fix guard.

    Serving a call and deciding the tier is on offer are two different callers
    of the same question. If only the key path learned to step aside, realtime
    would authenticate fine and the picker would still hide the tier.
    """

    async def test_availability_sees_the_row_that_authenticates(self):
        with _vault(
            openai_realtime=_Row("openai_realtime", False),
            openai=_Row("openai", True),
        ):
            row = await creds.active_credential(
                None, component="llm", provider="openai_realtime"
            )
        # managed_resolution asks is_known_bad about exactly this row.
        assert row.provider == "openai"
        assert row.last_check_ok is True

    async def test_the_key_path_decrypts_the_same_row(self):
        seen = {}

        class _Cipher:
            def decrypt(self, blob):
                seen["blob"] = blob
                return b"sk-from-the-openai-row"

        with (
            _vault(
                openai_realtime=_Row("openai_realtime", False),
                openai=_Row("openai", True),
            ),
            patch.object(creds, "_cipher", lambda: _Cipher()),
        ):
            key = await creds.resolve_api_key(
                None, component="llm", provider="openai_realtime"
            )

        assert key == "sk-from-the-openai-row"
        assert seen["blob"] == b"cipher-for-openai"


@pytest.mark.asyncio
class TestManagedAvailabilityAgrees:
    async def test_a_shadowed_tier_is_no_longer_withdrawn(self):
        """End of the chain: the picker offers realtime again."""
        from api.services.configuration import managed_resolution

        with _vault(
            openai_realtime=_Row("openai_realtime", False),
            openai=_Row("openai", True),
        ):
            bad = await managed_resolution._credential_is_known_bad(
                None, component="llm", provider="openai_realtime"
            )
        assert bad is False

    async def test_a_genuinely_bad_slot_is_still_withdrawn(self):
        from api.services.configuration import managed_resolution

        with _vault(openai=_Row("openai", False)):
            bad = await managed_resolution._credential_is_known_bad(
                None, component="llm", provider="openai"
            )
        assert bad is True
