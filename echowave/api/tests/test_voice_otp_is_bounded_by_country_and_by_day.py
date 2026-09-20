"""Two bounds on the verification call: where it may go, and how many.

The verification code goes out as a *voice call* on the platform's own shared
outbound pool. Decibyl pays for that carriage, and the caller chooses the
destination, so the endpoint is a dialler pointed wherever an authenticated
account types -- billed to us.

``normalise_number`` accepts any country: it prefixes 91 to a bare ten-digit
number and otherwise passes through whatever digits it was given, because its
job is making a comparison key for the DND list, not deciding who we will
call. So ``+1-900-…`` normalised cleanly and the platform dialled a premium-rate
line in another country. The two abuses that follow are the obvious ones --
international carriage at our expense, and a premium number whose payout goes to
whoever asked us to dial it.

The per-number caps that already exist do not touch this. Five sends per number
and a sixty-second cooldown bound what one *number* can be sent; they say
nothing about how many numbers an account can work through in an afternoon,
which is the whole of the volume abuse.

So: platform carriage goes to Indian mobiles only, and an account gets a
bounded number of verification sends per day. Neither applies when the
deployment is not putting calls on a platform line -- in dev, on the `log`
channel, nothing is dialled and nobody is billed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.services.compliance import dnd
from api.services.telephony import verified_numbers


class TestWhichNumbersPlatformCarriageWillDial:
    """``dnd.is_indian_mobile`` -- the predicate the gate is built on."""

    @pytest.mark.parametrize(
        "number",
        [
            "+91 98765 43210",
            "09876543210",
            "9876543210",
            "919876543210",
            "+91 6000000000",  # the 6-series is mobile too
            "+91 7012345678",
            "+91 8012345678",
        ],
    )
    def test_an_indian_mobile_is_dialable(self, number):
        assert dnd.is_indian_mobile(dnd.normalise_number(number)) is True

    @pytest.mark.parametrize(
        "number,why",
        [
            ("+1 900 555 1234", "US premium rate -- the payout abuse"),
            ("+1 415 555 1234", "ordinary US number -- international carriage"),
            ("+44 20 7946 0958", "UK landline"),
            ("+44 909 8790123", "UK premium rate"),
            ("+7 495 1234567", "Russia"),
            ("+234 800 1234567", "Nigeria"),
            ("+91 1800 123 4567", "Indian toll-free, not a mobile"),
            ("+91 140 1234567", "Indian telemarketing series, not a mobile"),
            ("+91 1234567890", "Indian landline series"),
        ],
    )
    def test_everything_else_is_refused(self, number, why):
        assert dnd.is_indian_mobile(dnd.normalise_number(number)) is False, why

    def test_a_number_that_did_not_normalise_is_refused(self):
        assert dnd.is_indian_mobile(None) is False
        assert dnd.is_indian_mobile("") is False

    def test_the_predicate_does_not_normalise_for_you(self):
        """It takes the normalised key, like every other caller of this module.

        A second normalisation path is how "verified" and "suppressed" stop
        being asked about the same string.
        """
        assert dnd.is_indian_mobile("+919876543210") is False


class TestTheCountryGate:
    async def test_a_foreign_number_is_refused_on_a_platform_line(self, monkeypatch):
        monkeypatch.setattr(verified_numbers, "_uses_platform_line", lambda: True)
        with pytest.raises(verified_numbers.NumberNotDialable):
            await verified_numbers.start_verification(
                1, "+1 900 555 1234", db=_FakeClient()
            )

    async def test_a_premium_indian_series_is_refused_on_a_platform_line(
        self, monkeypatch
    ):
        monkeypatch.setattr(verified_numbers, "_uses_platform_line", lambda: True)
        with pytest.raises(verified_numbers.NumberNotDialable):
            await verified_numbers.start_verification(
                1, "+91 1800 123 4567", db=_FakeClient()
            )

    async def test_an_indian_mobile_still_works(self, monkeypatch):
        monkeypatch.setattr(verified_numbers, "_uses_platform_line", lambda: True)
        started = await verified_numbers.start_verification(
            1, "+91 98765 43210", db=_FakeClient()
        )
        assert started.phone_number == "919876543210"

    async def test_off_a_platform_line_the_gate_does_not_apply(self, monkeypatch):
        """Nothing is dialled and nobody is billed on the `log` channel, so a
        developer verifying a foreign test number is not an abuse to stop."""
        monkeypatch.setattr(verified_numbers, "_uses_platform_line", lambda: False)
        started = await verified_numbers.start_verification(
            1, "+1 415 555 1234", db=_FakeClient()
        )
        assert started.phone_number == "14155551234"


class TestTheDailyCap:
    async def test_an_account_is_cut_off_after_its_daily_allowance(self, monkeypatch):
        monkeypatch.setattr(verified_numbers, "_uses_platform_line", lambda: True)
        client = _FakeClient()
        client.daily_sends = verified_numbers.VERIFICATION_MAX_DAILY_SENDS

        with pytest.raises(verified_numbers.DailyLimitReached):
            await verified_numbers.start_verification(1, "+91 98765 43210", db=client)

    async def test_the_last_send_inside_the_allowance_is_permitted(self, monkeypatch):
        """Off-by-one matters: a cap that refuses the Nth send advertises N
        and delivers N-1."""
        monkeypatch.setattr(verified_numbers, "_uses_platform_line", lambda: True)
        client = _FakeClient()
        client.daily_sends = verified_numbers.VERIFICATION_MAX_DAILY_SENDS - 1

        started = await verified_numbers.start_verification(
            1, "+91 98765 43210", db=client
        )
        assert started.code

    async def test_the_cap_is_counted_per_organization(self, monkeypatch):
        """Another account's traffic must not spend this one's allowance."""
        monkeypatch.setattr(verified_numbers, "_uses_platform_line", lambda: True)
        client = _FakeClient()
        client.daily_sends_by_org = {1: verified_numbers.VERIFICATION_MAX_DAILY_SENDS}

        with pytest.raises(verified_numbers.DailyLimitReached):
            await verified_numbers.start_verification(1, "+91 98765 43210", db=client)

        started = await verified_numbers.start_verification(
            2, "+91 98765 43210", db=client
        )
        assert started.code

    async def test_the_cap_is_asked_about_a_window_not_a_calendar_day(
        self, monkeypatch
    ):
        """A cap that resets at midnight UTC hands an abuser two allowances
        back to back across the boundary."""
        monkeypatch.setattr(verified_numbers, "_uses_platform_line", lambda: True)
        client = _FakeClient()
        moment = datetime(2026, 1, 2, 0, 5, tzinfo=UTC)

        await verified_numbers.start_verification(
            1, "+91 98765 43210", db=client, now=moment
        )
        assert client.since is not None
        assert client.since == moment - timedelta(days=1)

    async def test_off_a_platform_line_there_is_no_daily_cap(self, monkeypatch):
        monkeypatch.setattr(verified_numbers, "_uses_platform_line", lambda: False)
        client = _FakeClient()
        client.daily_sends = verified_numbers.VERIFICATION_MAX_DAILY_SENDS * 10

        started = await verified_numbers.start_verification(
            1, "+91 98765 43210", db=client
        )
        assert started.code


class _FakeClient:
    """Just enough of the DB client for ``start_verification``.

    The real one needs a database; what is under test here is the decision, and
    a fake makes "the cap was consulted with this window" assertable, which a
    real row cannot be.
    """

    def __init__(self):
        self.daily_sends = 0
        self.daily_sends_by_org: dict[int, int] = {}
        self.since: datetime | None = None
        self.upserts: list[tuple] = []

    async def get_verified_number(self, organization_id, number):
        return None

    async def list_verified_numbers(self, organization_id):
        return []

    async def count_verification_sends_since(self, organization_id, since):
        self.since = since
        if self.daily_sends_by_org:
            return self.daily_sends_by_org.get(organization_id, 0)
        return self.daily_sends

    async def upsert_verified_number_challenge(self, organization_id, number, **kwargs):
        self.upserts.append((organization_id, number, kwargs))
