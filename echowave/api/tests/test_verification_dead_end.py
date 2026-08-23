"""A number you are told to verify must be a number you can verify.

`REQUIRE_VERIFIED_TEST_NUMBER` is off by default and `constants.py` says why in
as many words: "a permission nobody can obtain is not a permission, it is an
outage". `VERIFICATION_CHANNEL` is `log` on every real deployment, and the log
sender refuses to run outside a dev ENVIRONMENT, so with the gate on nobody can
verify anything.

But `initiate_call` turns the same gate on for `using_shared_caller_id` —
accounts with no carrier of their own, which is every account on its first
call. Those accounts got "enter the code we send you", followed by a 502 on the
verify screen saying verification is not configured. The instruction and the
truth were on two different screens and only one of them was correct.

So the message now depends on whether a code can actually be delivered. This
pins that, and pins the delivery check itself, because the two must not drift:
a message promising a code the sender will refuse to send is the whole bug.
"""

from __future__ import annotations

import pytest

from api.services.telephony import verification_sender


class TestWhetherACodeCanReachAnyone:
    @pytest.mark.parametrize("environment", ["test", "local", "development", "dev"])
    def test_the_log_channel_counts_only_in_a_dev_environment(
        self, monkeypatch, environment
    ):
        monkeypatch.setattr(verification_sender, "VERIFICATION_CHANNEL", "log")
        monkeypatch.setattr(verification_sender, "ENVIRONMENT", environment)
        assert verification_sender.is_deliverable() is True

    @pytest.mark.parametrize("environment", ["production", "staging", ""])
    def test_the_log_channel_is_not_delivery_anywhere_else(
        self, monkeypatch, environment
    ):
        """The case that produced the dead end."""
        monkeypatch.setattr(verification_sender, "VERIFICATION_CHANNEL", "log")
        monkeypatch.setattr(verification_sender, "ENVIRONMENT", environment)
        assert verification_sender.is_deliverable() is False

    @pytest.mark.parametrize("channel", ["plivo_sms", "twilio_sms", "voice"])
    def test_a_real_channel_counts_in_production(self, monkeypatch, channel):
        monkeypatch.setattr(verification_sender, "VERIFICATION_CHANNEL", channel)
        monkeypatch.setattr(verification_sender, "ENVIRONMENT", "production")
        assert verification_sender.is_deliverable() is True

    def test_an_unknown_channel_is_not_delivery(self, monkeypatch):
        """`deliver_code` logs and refuses on an unknown channel, so promising a
        code for one would be the same broken promise in a new costume."""
        monkeypatch.setattr(verification_sender, "VERIFICATION_CHANNEL", "carrier-pigeon")
        monkeypatch.setattr(verification_sender, "ENVIRONMENT", "production")
        assert verification_sender.is_deliverable() is False


class TestTheCheckAgreesWithTheSender:
    """`is_deliverable()` is a cheap config read and `deliver_code` is the real
    thing. If they ever disagree the user is told one story and given another."""

    async def test_the_log_channel_refuses_exactly_when_undeliverable(
        self, monkeypatch
    ):
        monkeypatch.setattr(verification_sender, "VERIFICATION_CHANNEL", "log")
        monkeypatch.setattr(verification_sender, "ENVIRONMENT", "production")

        assert verification_sender.is_deliverable() is False
        result = await verification_sender.deliver_code("+919900000000", "123456")
        assert result.ok is False
        assert "not configured" in (result.error or "")

    async def test_the_log_channel_sends_exactly_when_deliverable(self, monkeypatch):
        monkeypatch.setattr(verification_sender, "VERIFICATION_CHANNEL", "log")
        monkeypatch.setattr(verification_sender, "ENVIRONMENT", "test")

        assert verification_sender.is_deliverable() is True
        result = await verification_sender.deliver_code("+919900000000", "123456")
        assert result.ok is True
