"""Thin means spent enough and left us under the floor; the mail says why."""

from api.services.billing import margin_watch
from api.tasks import margin_watch as job


def test_thin_needs_real_spend_and_a_margin_under_the_floor():
    # 30% margin on Rs 500: thin.
    assert margin_watch.is_thin(
        charged_paise=50_000,
        provider_cost_paise=35_000,
        floor_bps=4000,
        min_charged=20_000,
    )
    # 60% margin: fine.
    assert not margin_watch.is_thin(
        charged_paise=50_000,
        provider_cost_paise=20_000,
        floor_bps=4000,
        min_charged=20_000,
    )
    # Rs 50 of spend at 0% margin: noise, not an alarm.
    assert not margin_watch.is_thin(
        charged_paise=5_000,
        provider_cost_paise=5_000,
        floor_bps=4000,
        min_charged=20_000,
    )


def test_the_mail_names_the_account_the_margin_and_the_usual_causes():
    account = margin_watch.ThinAccount(
        organization_id=42,
        name="Kritilabs",
        charged_paise=50_000,
        provider_cost_paise=35_000,
    )
    assert account.margin_bps == 3000
    subject, body = job.compose(account)
    assert "Kritilabs" in subject and "30%" in subject
    assert "no rate on file" in body
    assert "/superadmin/billing/accounts/42" in body
