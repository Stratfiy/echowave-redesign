"""Naming the account in the staff accounts table.

The table identified an account as ``billing_name or provider_id``.
``billing_name`` is only set once somebody fills in the billing profile, so in
practice most rows read ``org_user-3f9c…``. Staff could see that an account
existed, was spending money and had gone idle, and could not tell whose it was
or write to them. Every operational question that starts at this table — a
failed payment, a low balance, an account that stopped calling — ends in
"contact the customer", and the address was not on the screen.

Ranked rather than filtered to Owner, so an organization created before the
founder fix still names somebody instead of showing a blank where a person
should be.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from api.db import billing_dashboard_client as dash
from api.db.models import (
    OrganizationMembershipModel,
    OrganizationModel,
    UserModel,
)
from api.enums import OrganizationRole

TODAY = date.today()
RANGE = {"start": TODAY - timedelta(days=30), "end": TODAY}


async def _account(async_session, slug: str, *, billing_name: str | None = None):
    org = OrganizationModel(
        provider_id=f"org_user-{slug}",
        billing_name=billing_name,
        quota_decibyl_tokens=0,
    )
    async_session.add(org)
    await async_session.flush()
    return org


async def _person(async_session, org, email: str, role: str, day: int):
    user = UserModel(provider_id=f"user-{email}", email=email)
    async_session.add(user)
    await async_session.flush()
    async_session.add(
        OrganizationMembershipModel(
            user_id=user.id,
            organization_id=org.id,
            role=role,
            created_at=datetime(2026, 1, day, tzinfo=UTC),
        )
    )
    await async_session.flush()
    return user


async def _row(async_session, org, **kwargs):
    accounts = await dash.accounts_summary(async_session, **RANGE, **kwargs)
    return next((a for a in accounts if a["organization_id"] == org.id), None)


@pytest.mark.asyncio
class TestTheTableNamesAPerson:
    async def test_it_shows_the_owner_email(self, async_session):
        org = await _account(async_session, "shows-owner")
        await _person(
            async_session,
            org,
            "founder@clinic.example",
            OrganizationRole.OWNER.value,
            1,
        )

        row = await _row(async_session, org)

        assert row["owner_email"] == "founder@clinic.example"
        assert row["owner_role"] == OrganizationRole.OWNER.value

    async def test_the_owner_outranks_an_earlier_member(self, async_session):
        """Whoever answers for the account, not whoever arrived first."""
        org = await _account(async_session, "owner-outranks")
        await _person(
            async_session, org, "early@clinic.example", OrganizationRole.MEMBER.value, 1
        )
        await _person(
            async_session, org, "boss@clinic.example", OrganizationRole.OWNER.value, 5
        )

        row = await _row(async_session, org)

        assert row["owner_email"] == "boss@clinic.example"

    async def test_an_ownerless_account_still_names_somebody(self, async_session):
        """Accounts created before the founder fix have no Owner at all. A
        blank here would hide exactly the accounts most likely to need help."""
        org = await _account(async_session, "ownerless")
        await _person(
            async_session, org, "solo@clinic.example", OrganizationRole.MEMBER.value, 1
        )

        row = await _row(async_session, org)

        assert row["owner_email"] == "solo@clinic.example"
        assert row["owner_role"] == OrganizationRole.MEMBER.value

    async def test_the_name_falls_back_to_the_email_not_the_provider_id(
        self, async_session
    ):
        """An account that has never filled in a billing profile used to read
        as `org_user-…`, which names nobody."""
        org = await _account(async_session, "no-billing-name")
        await _person(
            async_session,
            org,
            "unbilled@clinic.example",
            OrganizationRole.OWNER.value,
            1,
        )

        row = await _row(async_session, org)

        assert row["name"] == "unbilled@clinic.example"
        assert "org_user-" not in row["name"]

    async def test_a_billing_name_still_wins(self, async_session):
        """Once a customer tells us their company name, that is what staff
        should see — the email is the fallback, not a replacement."""
        org = await _account(
            async_session, "has-billing-name", billing_name="Sunrise Dental"
        )
        await _person(
            async_session,
            org,
            "founder@sunrise.example",
            OrganizationRole.OWNER.value,
            1,
        )

        row = await _row(async_session, org)

        assert row["name"] == "Sunrise Dental"
        assert row["owner_email"] == "founder@sunrise.example"

    async def test_it_counts_the_team(self, async_session):
        """A solo signup and a five-person team are different accounts to talk
        to, and the role tiers only mean anything on the second."""
        org = await _account(async_session, "counts")
        await _person(
            async_session, org, "one@team.example", OrganizationRole.OWNER.value, 1
        )
        await _person(
            async_session, org, "two@team.example", OrganizationRole.MEMBER.value, 2
        )
        await _person(
            async_session, org, "three@team.example", OrganizationRole.MEMBER.value, 3
        )

        row = await _row(async_session, org)

        assert row["member_count"] == 3

    async def test_one_row_per_account(self, async_session):
        """The contact join must not multiply the account out by its members —
        that would double-count revenue on every team account in the table."""
        org = await _account(async_session, "one-row")
        for i, email in enumerate(["a@x.example", "b@x.example", "c@x.example"]):
            await _person(
                async_session, org, email, OrganizationRole.MEMBER.value, i + 1
            )

        accounts = await dash.accounts_summary(async_session, **RANGE)

        assert len([a for a in accounts if a["organization_id"] == org.id]) == 1


@pytest.mark.asyncio
class TestFindingAnAccountByEmail:
    async def test_it_finds_an_account_by_the_owner_address(self, async_session):
        org = await _account(async_session, "find-owner")
        await _person(
            async_session, org, "findme@clinic.example", OrganizationRole.OWNER.value, 1
        )

        assert await _row(async_session, org, q="findme@clinic.example") is not None

    async def test_it_finds_an_account_by_a_members_address(self, async_session):
        """Whoever writes in is often not the founder. A search that only knew
        the owner would tell support the account does not exist."""
        org = await _account(async_session, "find-member")
        await _person(
            async_session, org, "boss@clinic.example", OrganizationRole.OWNER.value, 1
        )
        await _person(
            async_session, org, "ops@clinic.example", OrganizationRole.MEMBER.value, 2
        )

        assert await _row(async_session, org, q="ops@clinic.example") is not None

    async def test_a_partial_match_is_enough(self, async_session):
        """Support pastes a domain or half an address out of a ticket."""
        org = await _account(async_session, "partial")
        await _person(
            async_session,
            org,
            "someone@partialclinic.example",
            OrganizationRole.OWNER.value,
            1,
        )

        assert await _row(async_session, org, q="partialclinic") is not None

    async def test_case_does_not_matter(self, async_session):
        org = await _account(async_session, "casing")
        await _person(
            async_session, org, "mixed@Clinic.example", OrganizationRole.OWNER.value, 1
        )

        assert await _row(async_session, org, q="MIXED@clinic.EXAMPLE") is not None

    async def test_it_finds_an_account_by_billing_name(self, async_session):
        org = await _account(
            async_session, "by-billing-name", billing_name="Harbour Physiotherapy"
        )
        await _person(
            async_session, org, "who@harbour.example", OrganizationRole.OWNER.value, 1
        )

        assert await _row(async_session, org, q="harbour physio") is not None

    async def test_a_search_excludes_the_accounts_that_do_not_match(
        self, async_session
    ):
        """Otherwise the box looks like it works while returning everything."""
        wanted = await _account(async_session, "wanted")
        await _person(
            async_session, wanted, "wanted@x.example", OrganizationRole.OWNER.value, 1
        )
        other = await _account(async_session, "unwanted")
        await _person(
            async_session, other, "unwanted@y.example", OrganizationRole.OWNER.value, 1
        )

        assert await _row(async_session, wanted, q="wanted@x.example") is not None
        assert await _row(async_session, other, q="wanted@x.example") is None

    async def test_an_empty_search_is_not_a_filter(self, async_session):
        """A cleared search box must show everything again, not nothing."""
        org = await _account(async_session, "empty-search")
        await _person(
            async_session, org, "still@here.example", OrganizationRole.OWNER.value, 1
        )

        assert await _row(async_session, org, q="   ") is not None
        assert await _row(async_session, org, q=None) is not None
