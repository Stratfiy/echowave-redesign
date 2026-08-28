"""Do-not-disturb scrubbing and the TCCCPR calling window.

The properties worth holding onto, in the order they matter:

* A number a customer was asked to stop calling is not called, whatever it
  looked like when it was typed.
* A lookup that fails refuses the call. This gate fails **closed**, unlike the
  balance reservation path which deliberately fails open.
* The window is judged at dial time, not at queue time, because "now" moves
  between the two.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from api.db import db_client
from api.db.models import OrganizationModel
from api.services.compliance import dnd

IST = ZoneInfo("Asia/Kolkata")


async def _org(session, slug: str) -> int:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org.id


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 12, hour, minute, tzinfo=IST)


class TestNormalisation:
    """A list only protects anyone if what was uploaded and what is dialled
    reduce to the same key."""

    @pytest.mark.parametrize(
        "written",
        [
            "9876543210",
            "09876543210",
            "+91 98765 43210",
            "+919876543210",
            "0091-98765-43210",
            "(98765) 43210",
            "91 98765 43210",
        ],
    )
    def test_one_number_written_seven_ways_is_one_key(self, written):
        assert dnd.normalise_number(written) == "919876543210"

    def test_two_different_numbers_do_not_collide(self):
        assert dnd.normalise_number("9876543210") != dnd.normalise_number("9876543211")

    @pytest.mark.parametrize("junk", ["", None, "   ", "abcd", "12345", "+91"])
    def test_undialable_input_is_rejected_rather_than_stored(self, junk):
        """Storing a 5-digit "number" would make the list look longer than the
        protection it provides — it can never match a dialled number."""
        assert dnd.normalise_number(junk) is None


class TestCallingWindow:
    def test_inside_the_window(self):
        assert dnd.within_calling_hours(timezone_name="Asia/Kolkata", now=_at(10))

    @pytest.mark.parametrize("hour", [8, 21, 22, 3])
    def test_outside_the_window(self, hour):
        assert not dnd.within_calling_hours(timezone_name="Asia/Kolkata", now=_at(hour))

    def test_the_boundaries_are_inclusive_at_the_start_and_exclusive_at_the_end(
        self,
    ):
        assert dnd.within_calling_hours(timezone_name="Asia/Kolkata", now=_at(9, 0))
        assert not dnd.within_calling_hours(
            timezone_name="Asia/Kolkata", now=_at(21, 0)
        )

    def test_the_window_is_judged_in_the_organizations_zone(self):
        """16:00 UTC is 21:30 in Kolkata — outside the window there and inside
        it in London. Judging in the wrong zone is how a compliant deployment
        dials at half past nine at night."""
        moment = datetime(2026, 8, 12, 16, 0, tzinfo=ZoneInfo("UTC"))
        assert not dnd.within_calling_hours(timezone_name="Asia/Kolkata", now=moment)
        assert dnd.within_calling_hours(timezone_name="Europe/London", now=moment)

    def test_an_unknown_zone_falls_back_instead_of_blocking_every_call(self):
        """An organization typing a bad timezone into settings must not thereby
        switch off all of its outbound calling."""
        assert dnd.within_calling_hours(timezone_name="Mars/Olympus", now=_at(10))

    def test_a_window_that_wraps_midnight(self):
        assert dnd.within_calling_hours(
            timezone_name="Asia/Kolkata", now=_at(23), start="22:00", end="06:00"
        )
        assert not dnd.within_calling_hours(
            timezone_name="Asia/Kolkata", now=_at(12), start="22:00", end="06:00"
        )


class TestTheGate:
    async def test_an_unlisted_number_inside_the_window_passes(
        self, db_session, async_session
    ):
        org_id = await _org(async_session, "clean")
        result = await dnd.assert_may_call(
            org_id, "9876543210", timezone_name="Asia/Kolkata", now=_at(10)
        )
        # E.164, not the bare list key: both call sites dial whatever this
        # returns, and a carrier rejects the number without its '+'.
        assert result == "+919876543210"

    async def test_a_listed_number_is_refused(self, db_session, async_session):
        org_id = await _org(async_session, "listed")
        await db_client.add_dnd_entries(org_id, ["919876543210"])

        with pytest.raises(dnd.DoNotDisturbListed):
            await dnd.assert_may_call(
                org_id, "9876543210", timezone_name="Asia/Kolkata", now=_at(10)
            )

    async def test_the_list_matches_however_the_number_was_written(
        self, db_session, async_session
    ):
        """The whole point of normalising. Uploaded one way, dialled another."""
        org_id = await _org(async_session, "written-differently")
        await db_client.add_dnd_entries(
            org_id, [dnd.normalise_number("+91 98765 43210")]
        )

        with pytest.raises(dnd.DoNotDisturbListed):
            await dnd.assert_may_call(
                org_id, "09876543210", timezone_name="Asia/Kolkata", now=_at(10)
            )

    async def test_one_organizations_list_does_not_suppress_anothers_calls(
        self, db_session, async_session
    ):
        """These rows are personal data. A shared list would leak one
        customer's contacts into another's, and suppress calls the second
        customer was never asked to stop."""
        listed_org = await _org(async_session, "has-list")
        other_org = await _org(async_session, "no-list")
        await db_client.add_dnd_entries(listed_org, ["919876543210"])

        assert await dnd.assert_may_call(
            other_org, "9876543210", timezone_name="Asia/Kolkata", now=_at(10)
        )

    async def test_outside_the_window_is_refused_even_when_unlisted(
        self, db_session, async_session
    ):
        org_id = await _org(async_session, "late")
        with pytest.raises(dnd.OutsideCallingHours):
            await dnd.assert_may_call(
                org_id, "9876543210", timezone_name="Asia/Kolkata", now=_at(22)
            )

    async def test_a_failed_lookup_refuses_the_call(
        self, db_session, async_session, monkeypatch
    ):
        """This gate fails CLOSED, and that is the opposite of the balance
        reservation path. An unavailable list is not evidence that a number is
        absent from it, and here guessing wrong is a regulatory event rather
        than a lost call."""
        org_id = await _org(async_session, "db-down")

        class Broken:
            async def is_number_dnd_listed(self, *args, **kwargs):
                raise RuntimeError("database is down")

        with pytest.raises(dnd.DoNotDisturbListed):
            await dnd.assert_may_call(
                org_id,
                "9876543210",
                timezone_name="Asia/Kolkata",
                now=_at(10),
                db=Broken(),
            )

    async def test_an_undialable_number_never_reaches_the_carrier(
        self, db_session, async_session
    ):
        org_id = await _org(async_session, "malformed")
        with pytest.raises(dnd.CallRefused):
            await dnd.assert_may_call(
                org_id, "12345", timezone_name="Asia/Kolkata", now=_at(10)
            )

    async def test_refusals_carry_a_stable_machine_reason(
        self, db_session, async_session
    ):
        """The sentence shown to a customer will be rewritten; the token stored
        against the queued row and used in analytics must not move with it."""
        assert dnd.DoNotDisturbListed.reason == "dnd_listed"
        assert dnd.OutsideCallingHours.reason == "outside_calling_hours"


class TestTheList:
    async def test_adding_the_same_number_twice_is_idempotent(
        self, db_session, async_session
    ):
        """Re-uploading last month's file is the normal case, not the
        exception. Without this the table grows by its own size each upload."""
        org_id = await _org(async_session, "reupload")

        first = await db_client.add_dnd_entries(org_id, ["919876543210"])
        second = await db_client.add_dnd_entries(org_id, ["919876543210"])

        assert first == 1
        assert second == 0
        assert await db_client.count_dnd_entries(org_id) == 1

    async def test_duplicates_within_one_upload_are_collapsed(
        self, db_session, async_session
    ):
        org_id = await _org(async_session, "dupes-in-file")
        added = await db_client.add_dnd_entries(
            org_id, ["919876543210", "919876543210", "919876543211"]
        )
        assert added == 2

    async def test_removing_a_number_lets_the_call_through_again(
        self, db_session, async_session
    ):
        org_id = await _org(async_session, "removed")
        await db_client.add_dnd_entries(org_id, ["919876543210"])
        assert await db_client.remove_dnd_entry(org_id, "919876543210") is True

        assert await dnd.assert_may_call(
            org_id, "9876543210", timezone_name="Asia/Kolkata", now=_at(10)
        )


class TestWhatTheGateHandsBack:
    """The gate's return value is dialled directly. It has to be dialable.

    Both call sites — ``routes/telephony.initiate_call`` and the campaign
    dispatcher — do ``phone_number = await dnd.assert_may_call(...)`` and pass
    the result to the provider as ``to_number``. So the shape of this return
    value is not an internal detail; it is the number the carrier receives.
    """

    async def test_the_number_handed_back_is_e164(self, db_session, async_session):
        org_id = await _org(async_session, "dialable-form")
        result = await dnd.assert_may_call(
            org_id, "+91 98765 43210", timezone_name="Asia/Kolkata", now=_at(10)
        )
        assert result == "+919876543210"

    async def test_enforcement_disabled_still_returns_a_dialable_number(
        self, monkeypatch
    ):
        """The bypass path is the one nobody re-reads. It got this wrong too."""
        monkeypatch.setattr(dnd, "DND_ENFORCEMENT_ENABLED", False)
        assert await dnd.assert_may_call(1, "09876543210") == "+919876543210"

    def test_to_dialable_is_idempotent(self):
        assert dnd.to_dialable("919876543210") == "+919876543210"
        assert dnd.to_dialable("+919876543210") == "+919876543210"
        assert dnd.to_dialable(None) is None


class TestTheWindowProtectsStrangersNotTheAccountItself:
    """The calling window is about who is being rung, not who is ringing.

    TCCCPR sets 09:00-21:00 so a person is not called at night by someone they
    did not ask to hear from. A developer dialling their own verified handset
    to hear their own agent is not that person, and refusing them protects
    nobody while making the product untestable for half the working day.

    The exemption is proof-gated, not claim-gated: a number counts as the
    account's own only once a code has been sent to it and typed back.
    """

    async def test_a_verified_number_may_be_called_after_hours(
        self, db_session, async_session
    ):
        org_id = await _org(async_session, "own-handset-late")

        result = await dnd.assert_may_call(
            org_id,
            "9876543210",
            timezone_name="Asia/Kolkata",
            now=_at(22),
            enforce_calling_hours=False,
        )

        assert result == "+919876543210"

    async def test_the_window_still_applies_by_default(self, db_session, async_session):
        """Campaigns and the trigger API never pass the flag, so they keep it.

        This is the assertion that matters: the exemption has to be asked for
        explicitly, so a caller that dials numbers supplied as data cannot
        acquire it by accident.
        """
        org_id = await _org(async_session, "stranger-late")

        with pytest.raises(dnd.OutsideCallingHours):
            await dnd.assert_may_call(
                org_id, "9876543210", timezone_name="Asia/Kolkata", now=_at(22)
            )

    async def test_the_suppression_list_is_not_waived_with_the_window(
        self, db_session, async_session
    ):
        """Owning the handset is not a reason to overrule your own list.

        A number on the organization's do-not-disturb list is there because
        this account put it there. The window exemption is about the hour, not
        about consent.
        """
        org_id = await _org(async_session, "own-handset-but-listed")
        await db_client.add_dnd_entries(org_id, ["919876543210"])

        with pytest.raises(dnd.DoNotDisturbListed):
            await dnd.assert_may_call(
                org_id,
                "9876543210",
                timezone_name="Asia/Kolkata",
                now=_at(22),
                enforce_calling_hours=False,
            )

    async def test_the_exemption_does_not_let_an_undialable_number_through(
        self, db_session, async_session
    ):
        org_id = await _org(async_session, "own-handset-malformed")

        with pytest.raises(dnd.DoNotDisturbListed):
            await dnd.assert_may_call(
                org_id,
                "not-a-number",
                timezone_name="Asia/Kolkata",
                now=_at(22),
                enforce_calling_hours=False,
            )
