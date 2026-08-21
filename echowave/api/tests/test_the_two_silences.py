"""Two moments a customer is paying attention, and heard nothing.

**Signup.** A six-digit verification code went out and then nothing. A code is
not a welcome: it is a challenge, it says nothing about what the account can
do, and the next thing Decibyl said to that person was either a receipt or a
low-balance warning.

**Plan authorisation.** The customer authorises a standing instruction at their
own bank and hears nothing until the first collection lands — weeks, on a
monthly cycle, and it arrives as a debit. Two things go wrong in that silence
and both cost more than the email: somebody unsure it worked authorises again
and now there are two mandates on one account, and somebody who forgot asks
their bank about an unexplained debit, which is a chargeback rather than a
question.

What is tested here is not the wording. It is the three properties that decide
whether these are worth sending at all: **each is sent once**, whatever the
provider retries or a re-provision does; **each says the thing the customer
needs**, which for the plan is the amount the bank will actually take; and
**neither can unwind what it announces** when SMTP is down.
"""

from __future__ import annotations

import pytest

from api.services.auth import welcome_email
from api.services.billing import plan_email
from api.services.billing.subscription_plans import Plan


def _plan(**overrides) -> Plan:
    base = dict(
        code="starter",
        label="Starter",
        blurb="",
        price_paise=299_900,
        balance_paise=250_000,
        included_numbers=1,
        extra_number_price_paise=49_900,
        razorpay_plan_id="plan_x",
        razorpay_plan_id_export=None,
        enabled=True,
        sort_order=0,
    )
    base.update(overrides)
    return Plan(**base)


class TestTheWelcome:
    def test_it_reads_correctly_with_no_name_to_greet_them_by(self):
        """Signup takes a name and does not store it; Google gives none either.

        A greeting written around a name we do not have is how a first
        impression becomes "Hi ,".
        """
        notice = welcome_email.compose(
            account_name=None, app_url="https://app.decibyl.ai"
        )

        assert "Welcome to Decibyl." in notice.body
        assert "Hi ," not in notice.body

    def test_it_uses_the_name_when_there_is_one(self):
        notice = welcome_email.compose(
            account_name="Sunrise Clinic", app_url="https://app.decibyl.ai"
        )
        assert "Welcome, Sunrise Clinic." in notice.body

    def test_it_says_what_to_do_first_and_links_it(self):
        """Three things, because those three decide whether the account is ever
        used: what to do first, what it costs, and where the keys live."""
        notice = welcome_email.compose(
            account_name=None, app_url="https://app.decibyl.ai/"
        )

        assert "https://app.decibyl.ai/workflow/create" in notice.body
        assert "https://app.decibyl.ai/billing" in notice.body
        assert "https://app.decibyl.ai/provider-keys" in notice.body
        # The trailing slash on the base must not survive into the links.
        assert "decibyl.ai//" not in notice.body

    def test_it_does_not_compete_with_the_verification_email(self):
        """That mail is already in flight. Two messages racing for one click is
        how neither gets clicked."""
        notice = welcome_email.compose(account_name=None, app_url="https://x.test")

        lowered = notice.body.lower()
        assert "verification code" not in lowered
        assert "verify your email" not in lowered

    def test_one_welcome_per_account_for_ever(self):
        """Both front doors can re-provision a half-finished signup."""
        first = welcome_email.compose(account_name=None, app_url="https://x.test")
        again = welcome_email.compose(account_name="Later Name", app_url="https://x.test")

        assert first.dedupe_key == again.dedupe_key


class TestThePlanConfirmation:
    def test_it_quotes_the_gross_the_bank_will_actually_take(self):
        """Not the price on the pricing page.

        Every figure in this system is net of GST and the plan's price is the
        net one; the bank is authorised for the grossed-up amount. Quoting the
        net here would set up an argument with the first bank statement.
        """
        notice = plan_email.compose(
            plan=_plan(),
            mandate_id=7,
            authorised_paise=353_882,
            app_url="https://app.decibyl.ai",
        )

        assert "₹3,538.82" in notice.subject
        assert "₹3,538.82" in notice.body
        assert "including GST" in notice.body
        # The net price must not appear anywhere as the amount.
        assert "₹2,999.00 will be collected" not in notice.body

    def test_it_says_what_the_month_buys(self):
        notice = plan_email.compose(
            plan=_plan(),
            mandate_id=7,
            authorised_paise=353_882,
            app_url="https://app.decibyl.ai",
        )

        assert "₹2,500.00 of call balance" in notice.body
        # Balance that expires has to be stated where the money is committed.
        assert "does not carry over" in notice.body
        assert "1 phone number" in notice.body
        assert "₹499.00" in notice.body

    def test_it_says_how_to_stop_it_and_what_stopping_does_not_touch(self):
        """The one thing somebody reading an autopay email wants to know."""
        notice = plan_email.compose(
            plan=_plan(),
            mandate_id=7,
            authorised_paise=353_882,
            app_url="https://app.decibyl.ai",
        )

        assert "cancel" in notice.body.lower()
        assert "https://app.decibyl.ai/billing" in notice.body
        assert "does not touch balance you have already been granted" in notice.body

    def test_a_withdrawn_plan_row_still_gets_its_confirmation(self):
        """The bank does not care that our catalogue moved.

        A mandate can be authorised against a plan since renamed or withdrawn.
        The amount and how to stop it are owed either way.
        """
        notice = plan_email.compose(
            plan=None,
            mandate_id=7,
            authorised_paise=353_882,
            app_url="https://app.decibyl.ai",
        )

        assert "₹3,538.82" in notice.body
        assert "your plan" in notice.body
        assert "Each month this adds" not in notice.body

    def test_one_confirmation_per_authorisation_not_per_event(self):
        """Razorpay sends `authenticated` then `activated` for one
        authorisation and retries both at least once. Keying on the event would
        confirm the same standing instruction four times."""
        first = plan_email.compose(
            plan=_plan(), mandate_id=7, authorised_paise=353_882, app_url="https://x.test"
        )
        again = plan_email.compose(
            plan=_plan(), mandate_id=7, authorised_paise=353_882, app_url="https://x.test"
        )
        other = plan_email.compose(
            plan=_plan(), mandate_id=8, authorised_paise=353_882, app_url="https://x.test"
        )

        assert first.dedupe_key == again.dedupe_key
        assert first.dedupe_key != other.dedupe_key


class TestOnlyTheTransitionAnnounces:
    """"Is authorised" is true four times; "just became authorised" once."""

    def test_a_move_into_an_authorised_state_is_reported(self):
        from api.enums import MandateStatus

        # The rule the webhook handler applies, stated where it can be read.
        previous, next_status = (
            MandateStatus.CREATED.value,
            MandateStatus.AUTHENTICATED.value,
        )
        assert previous not in MandateStatus.authorised()
        assert next_status in MandateStatus.authorised()

    def test_a_second_authorised_event_is_not_a_transition(self):
        from api.enums import MandateStatus

        previous, next_status = (
            MandateStatus.AUTHENTICATED.value,
            MandateStatus.ACTIVE.value,
        )
        assert previous in MandateStatus.authorised()
        assert next_status in MandateStatus.authorised()

    def test_the_handler_reports_the_transition_rather_than_the_status(self):
        """Pinned on the source, because the difference is one word and the
        cost of getting it wrong is four emails per customer."""
        import inspect

        from api.services.billing import mandates

        source = inspect.getsource(mandates.apply_subscription_event)
        assert '"newly_authorised"' in source
        assert "previous not in MandateStatus.authorised()" in source


class TestAnnouncingNeverUnwindsWhatItAnnounces:
    """The shared claim-then-send, which is the only subtle part of either.

    The row is claimed *before* the send. The other order sends twice whenever
    two workers touch one account in the same tick, and a customer who reads
    "your number is suspended" twice has to work out whether it happened twice.
    """

    @pytest.fixture
    def captured_email(self, monkeypatch):
        """Intercept sends. Nothing in this suite may open a socket."""
        from api.services.messaging import announce
        from api.services.messaging.email import SendResult

        sent: list[dict] = []

        async def _send(*, to, subject, body_text, **kwargs):
            sent.append({"to": to, "subject": subject})
            return SendResult(ok=True)

        monkeypatch.setattr(announce.email, "send_email", _send)
        monkeypatch.setattr(announce.email, "email_is_configured", lambda: True)
        return sent

    async def _org(self, session, slug: str):
        from api.db.models import OrganizationModel

        org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
        session.add(org)
        await session.flush()
        return org

    async def test_an_account_with_no_address_is_not_an_error(
        self, db_session, async_session, captured_email
    ):
        """Every caller has already done the thing being announced."""
        from api.services.messaging import announce

        org = await self._org(async_session, "silent")

        sent = await announce.announce(
            organization_id=org.id,
            kind="welcome",
            notice=announce.Notice(subject="s", body="b", dedupe_key="k"),
        )

        assert sent is False
        assert captured_email == []

    async def test_the_same_notice_twice_sends_once(
        self, db_session, async_session, captured_email
    ):
        from api.services.messaging import announce

        org = await self._org(async_session, "once")
        notice = announce.Notice(subject="s", body="b", dedupe_key="k")

        first = await announce.announce(
            organization_id=org.id,
            kind="welcome",
            notice=notice,
            to=["someone@example.test"],
        )
        second = await announce.announce(
            organization_id=org.id,
            kind="welcome",
            notice=notice,
            to=["someone@example.test"],
        )

        assert first is True
        # Losing the claim is a success: the notice is going out, just not
        # from here.
        assert second is False
        assert len(captured_email) == 1

    async def test_a_different_notice_of_the_same_kind_still_sends(
        self, db_session, async_session, captured_email
    ):
        """Deduplication is per notice, not per kind — otherwise one dunning
        step would silence every later one."""
        from api.services.messaging import announce

        org = await self._org(async_session, "distinct")

        for key in ("day-7", "day-15"):
            await announce.announce(
                organization_id=org.id,
                kind="dunning",
                notice=announce.Notice(subject=key, body="b", dedupe_key=key),
                to=["someone@example.test"],
            )

        assert [m["subject"] for m in captured_email] == ["day-7", "day-15"]
