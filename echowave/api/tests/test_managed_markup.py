"""Changing the multiple charged on managed model usage.

One number sets the price of every managed call on every account. Making it
editable without a deploy is the point; it is also why this is the only setting
that needs a code from the company inbox before it applies.

Three properties this has to hold, and each fails in a way that costs money
rather than throwing:

**Effective-dated, never overwritten.** Re-costing a call from March must
reproduce what that customer was charged. A mutable setting silently rewrites
history the first time anyone edits it.

**The value is staged server-side.** The confirming request carries only a
code, so a replayed or tampered confirmation cannot substitute a different
multiple than the one the email described.

**The environment variable stays the seed.** An empty table must behave exactly
as the old constant did, or every install changes price on upgrade.
"""

from datetime import UTC, datetime, timedelta

import pytest

from api.constants import MANAGED_PROVIDER_MARKUP_BPS
from api.db.models import ManagedMarkupHistoryModel, MarkupChangeChallengeModel
from api.services.billing import markup


async def _user(async_session, slug: str):
    from api.db.models import UserModel

    user = UserModel(provider_id=f"user-{slug}")
    async_session.add(user)
    await async_session.flush()
    return user


@pytest.mark.asyncio
class TestResolving:
    async def test_an_empty_table_falls_back_to_the_environment(self, async_session):
        """A fresh install must price exactly as it did before this existed."""
        assert (
            await markup.resolve_markup_bps(async_session)
            == MANAGED_PROVIDER_MARKUP_BPS
        )

    async def test_the_open_row_wins(self, async_session):
        async_session.add(
            ManagedMarkupHistoryModel(
                markup_bps=16_000, effective_from=datetime.now(UTC)
            )
        )
        await async_session.flush()

        assert await markup.resolve_markup_bps(async_session) == 16_000

    async def test_an_old_call_prices_against_the_markup_of_its_day(
        self, async_session
    ):
        """The reason this is a history. Re-costing March against today's
        multiple would rewrite what a customer was actually charged."""
        march = datetime(2026, 3, 1, tzinfo=UTC)
        june = datetime(2026, 6, 1, tzinfo=UTC)
        async_session.add_all(
            [
                ManagedMarkupHistoryModel(
                    markup_bps=13_000, effective_from=march, effective_to=june
                ),
                ManagedMarkupHistoryModel(markup_bps=14_000, effective_from=june),
            ]
        )
        await async_session.flush()

        at_march = datetime(2026, 3, 15, tzinfo=UTC)
        assert await markup.resolve_markup_bps(async_session, at=at_march) == 13_000
        assert await markup.resolve_markup_bps(async_session) == 14_000


class TestBounds:
    def test_below_cost_is_refused(self):
        """Under 1.0x sells model usage for less than the vendor charged, on
        every managed call, until somebody notices the margin."""
        with pytest.raises(markup.MarkupError):
            markup.validate_markup_bps(9_999)

    def test_an_absurd_multiple_is_refused(self):
        """140000 instead of 14000 is one keystroke."""
        with pytest.raises(markup.MarkupError):
            markup.validate_markup_bps(140_000)

    def test_the_boundaries_themselves_are_allowed(self):
        assert markup.validate_markup_bps(markup.MIN_MARKUP_BPS) == 10_000
        assert markup.validate_markup_bps(markup.MAX_MARKUP_BPS) == 30_000

    def test_nonsense_is_refused_rather_than_coerced(self):
        with pytest.raises(markup.MarkupError):
            markup.validate_markup_bps("a lot")


@pytest.mark.asyncio
class TestStagingAChange:
    async def test_staging_applies_nothing(self, async_session):
        """The whole point of the two steps. Until the code comes back, the
        price every account pays is unchanged."""
        user = await _user(async_session, "stage")
        before = await markup.resolve_markup_bps(async_session)

        await markup.start_change(
            async_session, markup_bps=20_000, actor_user_id=user.id
        )

        assert await markup.resolve_markup_bps(async_session) == before

    async def test_the_code_is_not_stored(self, async_session):
        """Only a salted hash. A challenge table holding plaintext codes is a
        way for anyone with database access to change what every account pays."""
        user = await _user(async_session, "hash")
        started = await markup.start_change(
            async_session, markup_bps=20_000, actor_user_id=user.id
        )

        row = (
            await async_session.execute(
                __import__("sqlalchemy").select(MarkupChangeChallengeModel)
            )
        ).scalar_one()
        assert started.code not in (row.code_hash, row.code_salt)
        assert len(row.code_hash) == 64

    async def test_staging_again_replaces_the_pending_change(self, async_session):
        """One at a time, so an abandoned change is harmless and a stale code
        cannot apply a value nobody is looking at any more."""
        user = await _user(async_session, "replace")
        # Both values must differ from whatever is currently in force —
        # start_change refuses a no-op change — so derive them from the
        # current value rather than hardcoding. Hardcoded 17_000 here broke
        # the moment MANAGED_PROVIDER_MARKUP_BPS became 1.7x.
        current = await markup.resolve_markup_bps(async_session)
        first_bps = current + 1_000
        second_bps = current + 2_000

        first = await markup.start_change(
            async_session, markup_bps=first_bps, actor_user_id=user.id
        )
        await markup.start_change(
            async_session, markup_bps=second_bps, actor_user_id=user.id
        )

        with pytest.raises(markup.MarkupError):
            await markup.confirm_change(
                async_session, code=first.code, actor_user_id=user.id
            )

    async def test_a_change_to_the_current_value_is_refused(self, async_session):
        """Otherwise the audit trail fills with rows recording that nothing
        happened, and an operator gets an email about a change that is not one."""
        user = await _user(async_session, "noop")
        current = await markup.resolve_markup_bps(async_session)

        with pytest.raises(markup.MarkupError):
            await markup.start_change(
                async_session, markup_bps=current, actor_user_id=user.id
            )


@pytest.mark.asyncio
class TestConfirming:
    async def test_the_right_code_applies_the_staged_value(self, async_session):
        user = await _user(async_session, "confirm")
        started = await markup.start_change(
            async_session, markup_bps=20_000, actor_user_id=user.id
        )

        applied = await markup.confirm_change(
            async_session, code=started.code, actor_user_id=user.id
        )

        assert applied.markup_bps == 20_000
        assert await markup.resolve_markup_bps(async_session) == 20_000

    async def test_a_wrong_code_changes_nothing(self, async_session):
        user = await _user(async_session, "wrong")
        before = await markup.resolve_markup_bps(async_session)
        await markup.start_change(
            async_session, markup_bps=20_000, actor_user_id=user.id
        )

        with pytest.raises(markup.MarkupError):
            await markup.confirm_change(
                async_session, code="000000", actor_user_id=user.id
            )

        assert await markup.resolve_markup_bps(async_session) == before

    async def test_repeated_wrong_codes_cancel_the_change(self, async_session):
        """A code short enough to type is short enough to brute force, so the
        attempt ceiling is what makes it a second factor rather than a delay."""
        user = await _user(async_session, "bruteforce")
        started = await markup.start_change(
            async_session, markup_bps=20_000, actor_user_id=user.id
        )

        for _ in range(markup.MAX_ATTEMPTS):
            with pytest.raises(markup.MarkupError):
                await markup.confirm_change(
                    async_session, code="000000", actor_user_id=user.id
                )

        # Even the correct code no longer works — the change is gone.
        with pytest.raises(markup.MarkupError):
            await markup.confirm_change(
                async_session, code=started.code, actor_user_id=user.id
            )
        assert await markup.resolve_markup_bps(async_session) != 20_000

    async def test_an_expired_code_is_refused(self, async_session):
        user = await _user(async_session, "expired")
        started = await markup.start_change(
            async_session, markup_bps=20_000, actor_user_id=user.id
        )

        later = datetime.now(UTC) + timedelta(minutes=markup.CODE_TTL_MINUTES + 1)
        with pytest.raises(markup.MarkupError):
            await markup.confirm_change(
                async_session, code=started.code, actor_user_id=user.id, now=later
            )

    async def test_a_code_cannot_be_used_twice(self, async_session):
        user = await _user(async_session, "replay")
        started = await markup.start_change(
            async_session, markup_bps=20_000, actor_user_id=user.id
        )
        await markup.confirm_change(
            async_session, code=started.code, actor_user_id=user.id
        )

        with pytest.raises(markup.MarkupError):
            await markup.confirm_change(
                async_session, code=started.code, actor_user_id=user.id
            )

    async def test_confirming_with_nothing_staged_is_refused(self, async_session):
        user = await _user(async_session, "nothing")
        with pytest.raises(markup.MarkupError):
            await markup.confirm_change(
                async_session, code="123456", actor_user_id=user.id
            )


@pytest.mark.asyncio
class TestTheHistoryStaysConsistent:
    async def test_applying_closes_the_previous_row(self, async_session):
        """At most one markup in force. Two open rows would make "what does a
        call cost" have two answers, and the partial unique index would refuse
        the insert anyway."""
        user = await _user(async_session, "close")
        import sqlalchemy as sa

        for value in (20_000, 17_000):
            started = await markup.start_change(
                async_session, markup_bps=value, actor_user_id=user.id
            )
            await markup.confirm_change(
                async_session, code=started.code, actor_user_id=user.id
            )

        open_rows = (
            (
                await async_session.execute(
                    sa.select(ManagedMarkupHistoryModel).where(
                        ManagedMarkupHistoryModel.effective_to.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(open_rows) == 1
        assert open_rows[0].markup_bps == 17_000

    async def test_the_previous_value_is_reported_for_the_audit_row(
        self, async_session
    ):
        """The audit log records what it moved from as well as to, and the
        history row alone does not know the former."""
        user = await _user(async_session, "audit")
        started = await markup.start_change(
            async_session, markup_bps=20_000, actor_user_id=user.id
        )
        applied = await markup.confirm_change(
            async_session, code=started.code, actor_user_id=user.id
        )

        assert applied.previous_markup_bps == MANAGED_PROVIDER_MARKUP_BPS
        assert applied.markup_bps == 20_000


class TestTheEmail:
    def test_it_says_what_would_change_not_merely_that_something_would(self):
        """A code with no context is one somebody approves reflexively."""
        change = markup.StartedMarkupChange(
            markup_bps=14_000,
            previous_markup_bps=13_000,
            code="123456",
            expires_at=datetime.now(UTC),
        )
        body = markup.notice_body(change)

        assert "1.3x" in body
        assert "1.4x" in body
        assert "123456" in body

    def test_it_tells_the_reader_what_to_do_if_it_was_not_them(self):
        change = markup.StartedMarkupChange(
            markup_bps=14_000,
            previous_markup_bps=13_000,
            code="123456",
            expires_at=datetime.now(UTC),
        )

        assert "not you" in markup.notice_body(change).lower()

    def test_it_goes_to_the_operator_inbox_not_the_signed_in_user(self):
        """The second factor is access to the company inbox. Sending it to
        whoever is signed in would make it a formality."""
        assert markup.MARKUP_NOTICE_ADDRESS == "hello@decibyl.ai"
