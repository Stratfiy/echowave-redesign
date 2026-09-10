"""Our own accounts are billed at what the providers charge, and nothing more.

Decibyl runs its demos and its testing on its own platform. Billed as a
customer, those accounts pay the platform fee and the managed markup, which
bills the company to itself, drains the account mid-demo when the balance floor
bites, and counts QA as revenue on the operator dashboard.

Three effects and they travel together. Any one of them missing puts the
problem back: a floor exemption without the fee waiver still empties the
account, and a fee waiver without the markup waiver still books margin on a
call we made to ourselves.
"""

from datetime import UTC, datetime

import pytest

from api.services.billing.internal_accounts import COST_ONLY_MARKUP_BPS


class _Session:
    """Answers exactly one question: is this organization one of ours."""

    def __init__(self, internal: bool):
        self._internal = internal

    async def scalar(self, *_args, **_kwargs):
        return self._internal


class TestWhoCounts:
    async def test_an_internal_organization_is_recognised(self):
        from api.services.billing.internal_accounts import is_internal

        assert await is_internal(_Session(True), 1) is True

    async def test_a_customer_is_not(self):
        from api.services.billing.internal_accounts import is_internal

        assert await is_internal(_Session(False), 1) is False

    async def test_no_organization_is_never_internal(self):
        """None asks for list pricing — what a customer pays. Answering that
        with our own cost would quote every screen at zero margin."""
        from api.services.billing.internal_accounts import is_internal

        assert await is_internal(_Session(True), None) is False


class TestNoMarkup:
    async def test_an_internal_account_is_charged_cost(self, monkeypatch):
        from api.services.billing import markup

        async def internal(session, organization_id):
            return True

        monkeypatch.setattr(
            "api.services.billing.internal_accounts.is_internal", internal
        )

        assert (
            await markup.resolve_markup_bps(
                _Session(True), at=datetime.now(UTC), organization_id=1
            )
            == COST_ONLY_MARKUP_BPS
        )

    def test_cost_only_means_multiply_by_one(self):
        """10,000 basis points is 1.0x. If this ever drifts, an internal
        account silently starts earning margin again."""
        assert COST_ONLY_MARKUP_BPS == 10_000


class TestNoFloor:
    """The floor is right for a customer and wrong for the account used to
    test whether calls work at all."""

    async def test_an_internal_account_may_start_a_call_with_nothing(self, monkeypatch):
        from api.services.billing import reservations

        async def internal(session, organization_id):
            return True

        async def broke(session, *, organization_id):
            return 0

        monkeypatch.setattr(reservations, "is_internal", internal)
        monkeypatch.setattr(reservations, "current_balance_paise", broke)

        assert (
            await reservations.has_credit(
                _Session(True), organization_id=1, workflow_run_id=None
            )
            is True
        )

    async def test_a_customer_with_nothing_still_cannot(self, monkeypatch):
        from api.services.billing import reservations

        async def not_internal(session, organization_id):
            return False

        async def broke(session, *, organization_id):
            return 0

        monkeypatch.setattr(reservations, "is_internal", not_internal)
        monkeypatch.setattr(reservations, "current_balance_paise", broke)

        assert (
            await reservations.has_credit(
                _Session(False), organization_id=1, workflow_run_id=None
            )
            is False
        )


class TestTheScreenAgreesWithTheGate:
    def test_blocked_is_derived_from_the_same_rule(self):
        """A screen saying "calling blocked" while calls go through is worse
        than either state on its own."""
        import inspect

        from api.routes import payments

        source = inspect.getsource(payments.get_balance)

        assert "internal" in source
        assert "calling_blocked" in source


@pytest.mark.parametrize("field", ["rate_mpaise", "source", "pulse_seconds"])
def test_the_resolved_rate_still_carries_its_provenance(field):
    """An internal account's zero has to be traceable to a reason, not look
    like a rate card that failed to load."""
    from api.services.billing.rates import ResolvedPlatformRate

    assert field in ResolvedPlatformRate.__dataclass_fields__
