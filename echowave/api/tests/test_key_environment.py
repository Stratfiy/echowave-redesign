"""Whether an API key is allowed to ring a stranger.

Today one key does everything, so giving a developer or an agency API access
gives them the ability to dial your customers. The risk in fixing that is
getting a default backwards: a key that should be production reading as
sandbox takes a live integration down, and a key that should be sandbox
reading as production is the whole thing not working.
"""

import pytest

from api.services.auth.key_environment import (
    PRODUCTION,
    SANDBOX,
    environment_of_key,
    is_sandbox,
    normalise,
    prefix_for,
    refusal_message,
)


class TestExistingKeysKeepWorking:
    @pytest.mark.parametrize("raw", [None, "", "   ", "prod", "PRODUCTION_", 7, []])
    def test_anything_unrecognised_is_production(self, raw):
        """Every key issued before this was issued to do real work.

        A migration that quietly demoted them would take an account's
        integration down at the moment it deployed.
        """
        assert normalise(raw) == PRODUCTION

    def test_a_stored_null_is_production(self):
        assert is_sandbox(None) is False


class TestReadingTheStoredValue:
    @pytest.mark.parametrize("raw", ["sandbox", "SANDBOX", "  Sandbox  "])
    def test_sandbox_however_it_was_written(self, raw):
        assert normalise(raw) == SANDBOX

    def test_production_is_production(self):
        assert normalise("production") == PRODUCTION


class TestTheKeyLooksLikeWhatItIs:
    def test_a_sandbox_key_says_so_in_its_prefix(self):
        """A key in a config file, a log line or a support ticket should say
        which world it belongs to without anybody looking it up."""
        assert prefix_for(SANDBOX) == "dcb_test_"

    def test_a_production_key_keeps_the_prefix_it_always_had(self):
        assert prefix_for(PRODUCTION) == "dcb_"

    def test_the_two_are_told_apart_by_the_longer_one(self):
        """`dcb_test_...` also starts with `dcb_`, so order matters."""
        assert environment_of_key("dcb_test_abc123") == SANDBOX
        assert environment_of_key("dcb_abc123") == PRODUCTION


class TestReadingTheKeyStringIsAlwaysTheSaferWay:
    @pytest.mark.parametrize(
        "raw", [None, "", "garbage", "dcb", "DCB_TEST_abc", 7, "test_abc"]
    )
    def test_anything_unclear_reads_as_production(self, raw):
        """Deliberately the strict direction.

        This is used to warn, never to grant — the stored column is the
        authority. Reading an unclear key as production means it is treated as
        the more dangerous thing, which is the error worth making.
        """
        assert environment_of_key(raw) == PRODUCTION


class TestWhatSomebodyIsTold:
    def test_the_refusal_names_the_number_and_the_way_out(self):
        message = refusal_message("+919876543210")
        assert "+919876543210" in message
        assert "Verified numbers" in message
        assert "production key" in message
