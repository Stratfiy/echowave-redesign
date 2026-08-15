"""The telephony MCP tools, and the line they must not cross.

The most important test in this file asserts an absence: that provisioning and
release are **not** reachable over MCP. An agent that can buy numbers buys
several when it retries, and an agent that can release one destroys a phone
number that cannot be recovered and may be printed on a customer's van. The
read-only surface is the part that is safe to hand to a model.
"""

from __future__ import annotations

import pytest

from api.db.models import (
    CreditLedgerModel,
    OrganizationKycModel,
    OrganizationModel,
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
    UserModel,
)
from api.enums import (
    CreditLedgerKind,
    KycStatus,
    PhoneNumberStatus,
    RecurringChargeStatus,
)
from api.mcp_server.server import mcp
from api.mcp_server.tools import telephony_status
from api.services.billing import rentals


@pytest.fixture
def as_user(monkeypatch):
    """Authenticate every tool call as one user, without an API key round trip."""
    holder = {}

    async def _auth():
        return holder["user"]

    monkeypatch.setattr(telephony_status, "authenticate_mcp_request", _auth)
    return holder


async def _account(session, slug: str, *, status=KycStatus.CARRIER_APPROVED):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}")
    session.add_all([org, user])
    await session.flush()
    user.selected_organization_id = org.id
    session.add(
        OrganizationKycModel(
            organization_id=org.id,
            status=status.value,
            business_type="company",
            legal_name=f"{slug} Pvt Ltd",
            carrier="plivo",
            carrier_reference=f"app-{slug}",
        )
    )
    await session.flush()
    return org, user


class TestTheSurfaceIsReadOnly:
    async def test_no_provisioning_or_release_tool_is_registered(self):
        """The property this whole module is shaped around.

        If someone adds a buy or release tool later, this fails and they have
        to justify it deliberately rather than by accident.
        """
        tools = {t.name for t in await mcp._list_tools()}
        forbidden = {
            "provision_number",
            "buy_number",
            "purchase_number",
            "release_number",
            "delete_phone_number",
        }
        assert not (tools & forbidden), (
            f"A money-spending or irreversible telephony tool is exposed over "
            f"MCP: {sorted(tools & forbidden)}"
        )

    async def test_the_read_only_tools_are_registered(self):
        tools = {t.name for t in await mcp._list_tools()}
        assert {
            "get_telephony_verification",
            "list_phone_numbers",
            "search_available_numbers",
            "get_billing_summary",
        } <= tools

    async def test_they_are_annotated_read_only(self):
        """So a client that respects hints knows they change nothing."""
        by_name = {t.name for t in await mcp._list_tools()}
        assert "get_billing_summary" in by_name
        tool = await mcp.get_tool("get_billing_summary")
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False


class TestVerification:
    async def test_an_approved_account_reports_telephony_enabled(
        self, db_session, async_session, as_user
    ):
        org, user = await _account(async_session, "mcp-ok")
        as_user["user"] = user

        result = await telephony_status.get_telephony_verification()

        assert result["status"] == KycStatus.CARRIER_APPROVED.value
        assert result["telephony_enabled"] is True

    async def test_a_suspended_account_reports_why_it_cannot_call(
        self, db_session, async_session, as_user
    ):
        org, user = await _account(
            async_session, "mcp-susp", status=KycStatus.SUSPENDED
        )
        as_user["user"] = user

        result = await telephony_status.get_telephony_verification()

        assert result["telephony_enabled"] is False
        assert result["status"] == KycStatus.SUSPENDED.value

    async def test_an_unstarted_account_reports_what_is_missing(
        self, db_session, async_session, as_user
    ):
        org, user = await _account(
            async_session, "mcp-new", status=KycStatus.NOT_STARTED
        )
        as_user["user"] = user

        result = await telephony_status.get_telephony_verification()

        assert result["telephony_enabled"] is False
        assert set(result["missing_documents"]) == {
            "certificate_of_incorporation",
            "gst_certificate",
        }


class TestListNumbers:
    async def test_numbers_report_status_separately_from_the_customer_switch(
        self, db_session, async_session, as_user
    ):
        """is_active is theirs; status is ours. Conflating them would tell a
        customer their number is off when we suspended it, or vice versa."""
        org, user = await _account(async_session, "mcp-nums")
        as_user["user"] = user

        config = TelephonyConfigurationModel(
            organization_id=org.id,
            name="cfg",
            provider="plivo",
            credentials={"auth_id": "MA", "auth_token": "t"},
            is_platform_managed=True,
        )
        async_session.add(config)
        await async_session.flush()
        async_session.add(
            TelephonyPhoneNumberModel(
                organization_id=org.id,
                telephony_configuration_id=config.id,
                address="+918041110000",
                address_normalized="+918041110000",
                address_type="phone",
                country_code="IN",
                is_active=True,
                status=PhoneNumberStatus.SUSPENDED.value,
                carrier_number_id="cn-1",
            )
        )
        await async_session.flush()

        numbers = await telephony_status.list_phone_numbers()

        assert len(numbers) == 1
        assert numbers[0]["address"] == "+918041110000"
        assert numbers[0]["is_active"] is True
        assert numbers[0]["status"] == PhoneNumberStatus.SUSPENDED.value
        assert numbers[0]["carrier_number_id"] == "cn-1"

    async def test_an_account_with_no_numbers_returns_an_empty_list(
        self, db_session, async_session, as_user
    ):
        org, user = await _account(async_session, "mcp-empty")
        as_user["user"] = user
        assert await telephony_status.list_phone_numbers() == []


class TestBillingSummary:
    async def test_balance_and_monthly_rentals_are_reported_separately(
        self, db_session, async_session, as_user
    ):
        """A quiet month does not make a rental go away, and folding the two
        together would suggest it does."""
        org, user = await _account(async_session, "mcp-bill")
        as_user["user"] = user

        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=500000,
                kind=CreditLedgerKind.TOPUP.value,
                balance_after_paise=500000,
            )
        )
        config = TelephonyConfigurationModel(
            organization_id=org.id,
            name="cfg",
            provider="plivo",
            credentials={"auth_id": "MA", "auth_token": "t"},
            is_platform_managed=True,
        )
        async_session.add(config)
        await async_session.flush()
        number = TelephonyPhoneNumberModel(
            organization_id=org.id,
            telephony_configuration_id=config.id,
            address="+918041110001",
            address_normalized="+918041110001",
            address_type="phone",
            country_code="IN",
        )
        async_session.add(number)
        await async_session.flush()

        await rentals.open_number_rental(
            organization_id=org.id,
            phone_number_id=number.id,
            cost_paise=25000,
            price_paise=39900,
        )

        summary = await telephony_status.get_billing_summary()

        assert summary["balance_paise"] == 500000
        assert summary["balance_inr"] == 5000.0
        assert summary["monthly_recurring_paise"] == 39900
        assert len(summary["recurring_charges"]) == 1
        assert (
            summary["recurring_charges"][0]["status"]
            == RecurringChargeStatus.ACTIVE.value
        )
        assert summary["recurring_charges"][0]["days_overdue"] == 0

    async def test_an_account_with_nothing_reports_zeroes_not_an_error(
        self, db_session, async_session, as_user
    ):
        org, user = await _account(async_session, "mcp-nobill")
        as_user["user"] = user

        summary = await telephony_status.get_billing_summary()

        assert summary["balance_paise"] == 0
        assert summary["recurring_charges"] == []


class TestSearch:
    async def test_an_unverified_account_is_told_what_to_do_not_given_an_error(
        self, db_session, async_session, as_user
    ):
        """Returned rather than raised: "you are not verified" answers the
        question, and the sentence says how to fix it."""
        org, user = await _account(
            async_session, "mcp-search-unver", status=KycStatus.SUBMITTED
        )
        as_user["user"] = user

        config = TelephonyConfigurationModel(
            organization_id=org.id,
            name="cfg",
            provider="plivo",
            credentials={"auth_id": "MA", "auth_token": "t"},
            is_platform_managed=True,
        )
        async_session.add(config)
        await async_session.flush()

        result = await telephony_status.search_available_numbers(
            telephony_configuration_id=config.id
        )

        assert result["verification_required"] is True
        assert result["numbers"] == []

    async def test_a_configuration_from_another_org_is_not_searchable(
        self, db_session, async_session, as_user
    ):
        """Tenant isolation: an id in the request never implies ownership."""
        mine, my_user = await _account(async_session, "mcp-mine")
        theirs, _ = await _account(async_session, "mcp-theirs")
        as_user["user"] = my_user

        their_config = TelephonyConfigurationModel(
            organization_id=theirs.id,
            name="theirs",
            provider="plivo",
            credentials={"auth_id": "MA", "auth_token": "t"},
        )
        async_session.add(their_config)
        await async_session.flush()

        result = await telephony_status.search_available_numbers(
            telephony_configuration_id=their_config.id
        )

        assert result["numbers"] == []
        assert "error" in result
