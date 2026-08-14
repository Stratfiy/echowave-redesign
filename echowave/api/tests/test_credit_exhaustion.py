"""Telling an account the moment its credit actually runs out.

The low-balance warning fires while there is still money. This is the other
end, and until now it did not exist: an account whose credit ran out found out
when its next call was refused — mid campaign, with no explanation on the
screen anybody was watching.

The decision underneath it is worth stating plainly, because a negative balance
otherwise reads as a bug. **A live call is never cut off.** The person on the
phone is the customer's customer, and dropping them mid-sentence to save a few
rupees is worse for everybody than finishing the call and refusing the next
one. So the balance may go slightly negative — bounded by roughly one call,
since the reservation held most of it up front — the account is told
immediately, and the existing gate refuses the next run.

Two properties the tests hold, both about not being the sender people filter:

**Only on the crossing.** An account already at zero going further negative has
been told.

**One notice per account per day**, claimed before the send rather than after,
so two workers costing calls at the same moment produce one email.
"""

from datetime import UTC, datetime, timedelta

import pytest

from api.db.models import NotificationModel, OrganizationModel, UserModel
from api.services.billing import exhaustion


async def _org(async_session, slug):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    return org


@pytest.mark.asyncio
class TestWhenItFires:
    async def test_it_fires_on_the_call_that_crosses_zero(
        self, db_session, async_session
    ):
        org = await _org(async_session, "cross")

        assert await exhaustion.note_if_exhausted(
            async_session,
            organization_id=org.id,
            balance_before=500,
            balance_after=-200,
        )

    async def test_it_does_not_fire_while_there_is_still_credit(
        self, db_session, async_session
    ):
        """That is the low-balance warning's job, and it fires earlier so
        there is time to act."""
        org = await _org(async_session, "still")

        assert not await exhaustion.note_if_exhausted(
            async_session,
            organization_id=org.id,
            balance_before=5_000,
            balance_after=1_000,
        )

    async def test_an_account_already_at_zero_is_not_told_again(
        self, db_session, async_session
    ):
        """Repeating it on every call is the behaviour that makes people
        filter the sender, and then miss the one that mattered."""
        org = await _org(async_session, "already")

        assert not await exhaustion.note_if_exhausted(
            async_session,
            organization_id=org.id,
            balance_before=-100,
            balance_after=-900,
        )

    async def test_landing_exactly_on_zero_counts_as_running_out(
        self, db_session, async_session
    ):
        """Zero is out of credit. The next call is refused either way, so
        being silent because it did not go negative would leave the customer
        with the refusal and no warning."""
        org = await _org(async_session, "exact")

        assert await exhaustion.note_if_exhausted(
            async_session,
            organization_id=org.id,
            balance_before=1_000,
            balance_after=0,
        )


@pytest.mark.asyncio
class TestNotSendingTwice:
    async def test_a_second_crossing_the_same_day_is_silent(
        self, db_session, async_session
    ):
        """A busy account can cross zero, top up, and cross again. Three
        emails in an afternoon is how a useful warning becomes noise."""
        org = await _org(async_session, "twice")

        first = await exhaustion.note_if_exhausted(
            async_session,
            organization_id=org.id,
            balance_before=500,
            balance_after=-100,
        )
        second = await exhaustion.note_if_exhausted(
            async_session,
            organization_id=org.id,
            balance_before=500,
            balance_after=-100,
        )

        assert first is True
        assert second is False

    async def test_the_next_day_can_be_told_again(self, db_session, async_session):
        """Running out again tomorrow is news again."""
        org = await _org(async_session, "tomorrow")
        today = datetime.now(UTC)

        await exhaustion.note_if_exhausted(
            async_session,
            organization_id=org.id,
            balance_before=500,
            balance_after=-100,
            now=today,
        )
        again = await exhaustion.note_if_exhausted(
            async_session,
            organization_id=org.id,
            balance_before=500,
            balance_after=-100,
            now=today + timedelta(days=1),
        )

        assert again is True

    async def test_the_claim_is_made_before_the_send(self, db_session, async_session):
        """The row exists as soon as the caller is told to send, not after —
        which is what makes two workers racing produce one email rather than
        two."""
        import sqlalchemy as sa

        org = await _org(async_session, "claim")
        await exhaustion.note_if_exhausted(
            async_session,
            organization_id=org.id,
            balance_before=500,
            balance_after=-100,
        )

        row = (
            await async_session.execute(
                sa.select(NotificationModel).where(
                    NotificationModel.organization_id == org.id
                )
            )
        ).scalar_one()
        assert row.kind == "credit_exhausted"

    async def test_two_accounts_are_told_independently(self, db_session, async_session):
        mine = await _org(async_session, "mine-ex")
        theirs = await _org(async_session, "theirs-ex")

        assert await exhaustion.note_if_exhausted(
            async_session,
            organization_id=mine.id,
            balance_before=500,
            balance_after=-100,
        )
        assert await exhaustion.note_if_exhausted(
            async_session,
            organization_id=theirs.id,
            balance_before=500,
            balance_after=-100,
        )


@pytest.mark.asyncio
class TestWhoIsTold:
    async def test_an_owner_is_preferred_over_a_member(self, db_session, async_session):
        """The same order the roster shows, so the person who gets the mail is
        the one a customer would expect to."""
        from api.db.models import OrganizationMembershipModel

        org = await _org(async_session, "who")
        member = UserModel(provider_id="u-member", email="member@example.com")
        owner = UserModel(provider_id="u-owner", email="owner@example.com")
        async_session.add_all([member, owner])
        await async_session.flush()
        async_session.add_all(
            [
                OrganizationMembershipModel(
                    user_id=member.id, organization_id=org.id, role="member"
                ),
                OrganizationMembershipModel(
                    user_id=owner.id, organization_id=org.id, role="owner"
                ),
            ]
        )
        await async_session.flush()

        assert (
            await exhaustion.recipient_for(async_session, organization_id=org.id)
            == "owner@example.com"
        )

    async def test_an_account_with_nobody_contactable_returns_none(
        self, db_session, async_session
    ):
        """Worth knowing the notice could not be delivered; not worth failing
        the costing job that discovered it."""
        org = await _org(async_session, "nobody")

        assert (
            await exhaustion.recipient_for(async_session, organization_id=org.id)
            is None
        )


class TestTheEmail:
    def test_it_says_it_has_run_out_not_that_it_is_low(self):
        """A warning that understates the situation is one somebody deals with
        tomorrow. It has already happened."""
        body = exhaustion.notice_body(balance_paise=-250)

        assert "run out" in body.lower()
        assert "low" not in body.lower()

    def test_it_says_the_live_call_was_not_cut_off(self):
        """The decision, stated where the customer sees it — otherwise a
        negative balance reads as a system that lost track."""
        body = exhaustion.notice_body(balance_paise=-250)

        assert "finished normally" in body

    def test_it_says_what_happens_next_and_where_to_go(self):
        body = exhaustion.notice_body(balance_paise=-250)

        assert "refused" in body
        assert "/billing" in body

    def test_a_balance_of_exactly_zero_does_not_claim_a_debt(self):
        """Nothing is owed at zero, and telling somebody they are in arrears
        when they are not is its own support ticket."""
        body = exhaustion.notice_body(balance_paise=0)

        assert "₹0.00" not in body


@pytest.mark.asyncio
class TestItActuallyFiresFromCosting:
    """The wiring, not the unit.

    ``note_if_exhausted`` passing on its own proves nothing about whether
    anything calls it. This drives a real debit through the costing path and
    checks the notice appeared — the failure this catches is a correct function
    nobody invokes, which is indistinguishable from no feature at all.
    """

    async def test_costing_a_call_that_empties_the_account_notifies(
        self, db_session, async_session, monkeypatch
    ):
        import sqlalchemy as sa

        from api.db.models import CreditLedgerModel
        from api.enums import CreditLedgerKind
        from api.services.billing import costing

        sent = {}

        async def _fake_send(**kwargs):
            from api.services.messaging.email import SendResult

            sent.update(kwargs)
            return SendResult(ok=True)

        monkeypatch.setattr("api.services.billing.costing.send_email", _fake_send)

        org = await _org(async_session, "wired")
        user = UserModel(provider_id="u-wired", email="owner-wired@example.com")
        async_session.add(user)
        await async_session.flush()
        from api.db.models import OrganizationMembershipModel

        async_session.add(
            OrganizationMembershipModel(
                user_id=user.id, organization_id=org.id, role="owner"
            )
        )
        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=1_000,
                kind=CreditLedgerKind.TOPUP.value,
                balance_after_paise=1_000,
            )
        )
        await async_session.flush()

        # The private debit, called directly. The public entry point needs a
        # costed run with rate rows behind it; this is the narrowest thing that
        # exercises the wiring under test.
        await costing._debit_ledger(
            async_session,
            organization_id=org.id,
            workflow_run_id=1,
            amount_paise=1_500,
            recost=False,
        )
        await async_session.flush()

        notices = (
            (
                await async_session.execute(
                    sa.select(NotificationModel).where(
                        NotificationModel.organization_id == org.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [n.kind for n in notices] == ["credit_exhausted"]
        assert sent.get("to") == "owner-wired@example.com"
