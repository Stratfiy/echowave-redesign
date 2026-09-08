"""Taking money from a customer who is not watching.

Every test here is a way to charge somebody twice, charge them too much, or
charge a card that has already said no three times. That is the entire risk
surface of an automatic debit, and none of it involves the provider — it is all
decided before a request is made, which is why it can be tested exhaustively
and why it is worth doing so.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.constants import MIN_TOPUP_PAISE
from api.services.billing import auto_topup
from api.services.billing.auto_topup import (
    CHARGE,
    COOLDOWN_HOURS,
    MAX_CONSECUTIVE_FAILURES,
    NOTICE_HOURS,
    SCHEDULE,
    SKIP,
    Account,
    Decision,
    History,
    Settings,
    decide,
)

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
AMOUNT = max(100_000, MIN_TOPUP_PAISE)


def _settings(**kw) -> Settings:
    base = dict(
        enabled=True,
        trigger_days=5,
        trigger_paise=15_000,
        amount_paise=AMOUNT,
        monthly_cap_paise=AMOUNT * 4,
        max_per_month=4,
    )
    base.update(kw)
    return Settings(**base)


def _account(**kw) -> Account:
    base = dict(
        balance_paise=1_000,
        daily_burn_paise=10_000,
        mandate_max_paise=AMOUNT * 2,
        mandate_is_live=True,
    )
    base.update(kw)
    return Account(**base)


def _decide(settings=None, account=None, history=None, now=NOW) -> Decision:
    return decide(
        settings=settings or _settings(),
        account=account or _account(),
        history=history or History(),
        now=now,
    )


class TestTheHappyPathIsAScheduleNotACharge:
    def test_reaching_the_trigger_schedules_rather_than_charges(self):
        """India's e-mandate rules require a clear 24 hours' notice before each
        debit. A design that charges the moment a balance drops cannot comply,
        so the first decision is never to take money."""
        decision = _decide()
        assert decision.action == SCHEDULE
        assert decision.will_take_money is False
        assert decision.amount_paise == AMOUNT

    def test_the_debit_is_set_beyond_the_required_notice(self):
        decision = _decide()
        assert decision.charge_after >= NOW + timedelta(hours=24)

    def test_the_notice_has_an_hour_of_headroom(self):
        """A sweep that runs a few minutes late must not shorten the notice
        below what is required — the margin is the compliance, not the intent."""
        assert NOTICE_HOURS > 24


class TestWhenTheNoticeHasBeenGiven:
    def test_the_debit_runs_once_the_clock_has_elapsed(self):
        history = History(notified_at=NOW - timedelta(hours=NOTICE_HOURS + 1))
        decision = _decide(history=history)
        assert decision.action == CHARGE
        assert decision.amount_paise == AMOUNT

    def test_nothing_happens_before_the_clock_elapses(self):
        history = History(notified_at=NOW - timedelta(hours=1))
        decision = _decide(history=history)
        assert decision.action == SKIP
        assert "notice" in decision.reason

    def test_a_recovered_balance_does_not_cancel_a_noticed_debit(self):
        """Deliberate, and worth stating: the customer was told this debit was
        coming and authorised this amount. Silently dropping it because they
        topped up by hand in the meantime would make the notice a lie in the
        other direction — and the credit is not lost, it is credit."""
        history = History(notified_at=NOW - timedelta(hours=NOTICE_HOURS + 1))
        rich = _account(balance_paise=10_000_000, daily_burn_paise=1)
        assert _decide(account=rich, history=history).action == CHARGE


class TestRefusingToChargeTwice:
    def test_an_attempt_in_flight_blocks_another(self):
        """The balance stays below the trigger for the whole notice period, so
        without this every sweep in that window starts a fresh debit."""
        decision = _decide(history=History(in_flight=True))
        assert decision.action == SKIP
        assert "in flight" in decision.reason

    def test_a_recent_success_is_a_cooldown(self):
        """A campaign can burn a whole top-up in minutes. A balance that is low
        again is indistinguishable from credit that never arrived."""
        history = History(last_success_at=NOW - timedelta(hours=1))
        assert _decide(history=history).action == SKIP

    def test_the_cooldown_does_expire(self):
        history = History(last_success_at=NOW - timedelta(hours=COOLDOWN_HOURS + 1))
        assert _decide(history=history).action == SCHEDULE


class TestTheRunawayCeiling:
    def test_the_monthly_count_is_a_hard_stop(self):
        history = History(count_this_month=4)
        decision = _decide(history=history)
        assert decision.action == SKIP
        assert "times this month" in decision.reason

    def test_the_monthly_money_cap_is_a_hard_stop(self):
        """The count alone does not bound the money if somebody raises the
        amount mid-month, and the money alone does not bound a fast loop."""
        history = History(charged_this_month_paise=AMOUNT * 4)
        assert _decide(history=history).action == SKIP

    def test_a_charge_that_would_cross_the_cap_is_refused_whole(self):
        """Not part-charged to fit. A partial top-up nobody asked for is a
        surprise on a statement and buys an amount the customer did not choose."""
        settings = _settings(monthly_cap_paise=AMOUNT + 1)
        history = History(charged_this_month_paise=AMOUNT)
        assert _decide(settings=settings, history=history).action == SKIP

    def test_a_zero_cap_means_no_money_ceiling_only_a_count_one(self):
        settings = _settings(monthly_cap_paise=0)
        history = History(charged_this_month_paise=10_000_000)
        assert _decide(settings=settings, history=history).action == SCHEDULE


class TestRefusingADeadInstrument:
    def test_it_stops_after_consecutive_failures(self):
        """Declines are not free: banks charge for some, and a merchant that
        keeps presenting dead cards gets flagged by its processor."""
        history = History(consecutive_failures=MAX_CONSECUTIVE_FAILURES)
        decision = _decide(history=history)
        assert decision.action == SKIP
        assert "consecutive failures" in decision.reason

    def test_it_keeps_going_below_that_line(self):
        history = History(consecutive_failures=MAX_CONSECUTIVE_FAILURES - 1)
        assert _decide(history=history).action == SCHEDULE

    def test_no_live_authorisation_means_nothing_to_charge(self):
        assert _decide(account=_account(mandate_is_live=False)).action == SKIP


class TestTheAmountItself:
    def test_an_amount_below_the_platform_minimum_is_refused(self):
        settings = _settings(amount_paise=MIN_TOPUP_PAISE - 1)
        assert _decide(settings=settings).action == SKIP

    def test_an_amount_above_the_authorised_maximum_is_refused_here(self):
        """The bank would refuse it anyway. Refusing it here means the customer
        is told to raise their authorisation instead of shown a decline."""
        account = _account(mandate_max_paise=AMOUNT - 1)
        decision = _decide(account=account)
        assert decision.action == SKIP
        assert "authorised maximum" in decision.reason

    def test_an_amount_exactly_at_the_authorised_maximum_is_allowed(self):
        account = _account(mandate_max_paise=AMOUNT)
        assert _decide(account=account).action == SCHEDULE


class TestWhenAnAccountQualifies:
    def test_runway_is_what_normally_triggers_it(self):
        """It scales. An account spending Rs 5,000 a day and one spending Rs 50
        need topping up at wildly different balances."""
        account = _account(balance_paise=40_000, daily_burn_paise=10_000)  # 4 days
        assert auto_topup.is_below_trigger(_settings(), account) is True

    def test_plenty_of_runway_does_not_trigger(self):
        account = _account(balance_paise=1_000_000, daily_burn_paise=10_000)
        assert auto_topup.is_below_trigger(_settings(), account) is False
        assert _decide(account=account).action == SKIP

    def test_the_absolute_floor_catches_an_account_with_no_burn(self):
        """A runway of "infinite" on Rs 40 of credit is not a useful answer, and
        it is the state every brand-new account is in."""
        account = _account(balance_paise=4_000, daily_burn_paise=0)
        assert auto_topup.is_below_trigger(_settings(), account) is True

    def test_a_quiet_account_with_real_credit_is_left_alone(self):
        account = _account(balance_paise=500_000, daily_burn_paise=0)
        assert auto_topup.is_below_trigger(_settings(), account) is False


class TestOffByDefault:
    def test_disabled_is_the_default(self):
        assert Settings().enabled is False

    def test_disabled_refuses_before_anything_else_is_considered(self):
        """Including a noticed debit. Turning it off has to mean off."""
        history = History(notified_at=NOW - timedelta(days=3))
        decision = _decide(settings=_settings(enabled=False), history=history)
        assert decision.action == SKIP

    def test_a_default_configuration_cannot_charge_anything(self):
        """The default amount is zero, so an account that somehow enables it
        without choosing an amount takes no money."""
        assert Settings().amount_paise == 0


class TestEveryRefusalSaysWhy:
    @pytest.mark.parametrize(
        "history",
        [
            History(in_flight=True),
            History(consecutive_failures=9),
            History(count_this_month=99),
            History(last_success_at=NOW),
        ],
        ids=["in-flight", "failures", "count", "cooldown"],
    )
    def test_a_skip_is_never_silent(self, history):
        """A top-up that did not happen is a question somebody will ask, and
        "it just didn't" is not an answer that survives a support ticket."""
        decision = _decide(history=history)
        assert decision.action == SKIP
        assert decision.reason.strip()
        assert decision.will_take_money is False
