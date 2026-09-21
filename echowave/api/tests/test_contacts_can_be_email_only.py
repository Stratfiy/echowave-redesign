"""OP-3: a contact carries a number, an address, or both, never neither.

A prospect found on the web has an address and no number, and the book
refused it. Now it imports, upserts on its address, is found by it, and
is shown by it where a number would have been.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.db import db_client
from api.db.models import ContactListModel, ContactModel, OrganizationModel
from api.services.contacts.csv_import import parse_contacts_csv
from api.services.workflow import contact_lookup


class TestTheImporter:
    def test_an_email_only_file_imports(self):
        out = parse_contacts_csv(
            b"name,email,company\nPriya,priya@sunrise.example,Sunrise\nRavi,,\n"
        )
        assert out.phone_column is None
        assert out.email_column == "email"
        assert out.rows == [
            {
                "phone_raw": None,
                "phone_normalized": None,
                "email": "priya@sunrise.example",
                "name": "Priya",
                "attributes": {"company": "Sunrise"},
            }
        ]
        assert out.skipped == 1
        assert "No phone number or email address" in out.problems[0][1]

    def test_a_file_with_both_keeps_both_and_the_address_is_not_an_attribute(self):
        out = parse_contacts_csv(
            b"phone,email\n+91 98765 43210,Ravi@Acme.example\n", country_hint="IN"
        )
        row = out.rows[0]
        assert row["phone_normalized"] == "+919876543210"
        assert row["email"] == "Ravi@Acme.example"
        assert row["attributes"] == {}

    def test_a_broken_address_beside_a_number_is_noted_not_refused(self):
        out = parse_contacts_csv(
            b"phone,email\n+91 98765 43210,not-an-address\n", country_hint="IN"
        )
        assert len(out.rows) == 1
        assert out.rows[0]["email"] is None
        assert "kept without it" in out.problems[0][1]

    def test_a_broken_address_alone_is_refused(self):
        out = parse_contacts_csv(b"email\nnot-an-address\n")
        assert out.rows == []
        assert "is not an email address" in out.problems[0][1]

    def test_the_same_address_twice_is_once(self):
        out = parse_contacts_csv(b"email\na@x.example\nA@X.example\n")
        assert len(out.rows) == 1
        assert "appears earlier" in out.problems[0][1]

    def test_neither_column_is_refused_with_both_named(self):
        out = parse_contacts_csv(b"name,city\nRavi,Chennai\n")
        assert out.rows == []
        assert "No phone or email column" in out.problems[0][1]


@contextlib.asynccontextmanager
async def _same(session):
    yield session


@pytest.fixture
def book(async_session):
    """The client on the test's own session, so what the test wrote is what
    the client reads and everything rolls back together."""
    with patch.object(db_client, "async_session", lambda: _same(async_session)):
        yield async_session


class TestTheBook:
    async def _list(self, async_session, tag: str):
        org = OrganizationModel(provider_id=f"org-{tag}", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
        lst = ContactListModel(organization_id=org.id, name="Prospects")
        async_session.add(lst)
        await async_session.flush()
        return org.id, lst.id

    async def test_an_email_only_contact_is_stored_and_refreshed_by_address(self, book):
        org_id, list_id = await self._list(book, "email-only")
        written, skipped = await db_client.upsert_contacts(
            list_id,
            organization_id=org_id,
            rows=[
                {"email": "Priya@Sunrise.example", "name": "Priya"},
                {"phone_raw": "+91 98765 43210", "phone_normalized": "+919876543210"},
                {"name": "nobody"},
            ],
        )
        assert (written, skipped) == (2, 1)
        again, _ = await db_client.upsert_contacts(
            list_id,
            organization_id=org_id,
            rows=[{"email": "priya@sunrise.example", "name": "Dr. Priya Raman"}],
        )
        assert again == 1
        rows = (
            (
                await book.execute(
                    select(ContactModel).where(ContactModel.contact_list_id == list_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
        priya = await db_client.find_contact_by_email(
            list_id, "priya@sunrise.example", organization_id=org_id
        )
        assert priya is not None
        assert priya.name == "Dr. Priya Raman"
        assert priya.phone_normalized is None
        assert priya.email == "priya@sunrise.example"

    async def test_a_number_row_refreshes_its_address_too(self, book):
        org_id, list_id = await self._list(book, "both")
        await db_client.upsert_contacts(
            list_id,
            organization_id=org_id,
            rows=[{"phone_raw": "98765 43210", "phone_normalized": "+919876543210"}],
        )
        await db_client.upsert_contacts(
            list_id,
            organization_id=org_id,
            rows=[
                {
                    "phone_raw": "98765 43210",
                    "phone_normalized": "+919876543210",
                    "email": "ravi@acme.example",
                }
            ],
        )
        row = await db_client.find_contact_by_phone(list_id, "+919876543210")
        assert row.email_normalized == "ravi@acme.example"

    async def test_the_database_refuses_a_contact_with_neither(self, book):
        org_id, list_id = await self._list(book, "neither")
        with pytest.raises(IntegrityError):
            async with book.begin_nested():
                book.add(
                    ContactModel(
                        organization_id=org_id, contact_list_id=list_id, name="x"
                    )
                )
                await book.flush()

    async def test_found_by_address_across_the_account(self, book):
        org_id, list_id = await self._list(book, "search")
        await db_client.upsert_contacts(
            list_id,
            organization_id=org_id,
            rows=[{"email": "priya@sunrise.example", "name": "Priya"}],
        )
        rows = await db_client.search_contacts_for_organization(
            org_id, ["sunrise.example"]
        )
        assert [r.email for r in rows] == ["priya@sunrise.example"]


class TestHowAContactReads:
    def test_the_line_shows_the_address_when_there_is_no_number(self):
        line = contact_lookup._line(
            SimpleNamespace(
                name="Priya",
                phone_raw=None,
                phone_normalized=None,
                email="priya@sunrise.example",
                attributes={},
            )
        )
        assert line.startswith("- Priya (priya@sunrise.example)")

    def test_the_line_shows_both_when_both(self):
        line = contact_lookup._line(
            SimpleNamespace(
                name="Ravi",
                phone_raw="+91 98765 43210",
                phone_normalized=None,
                email="ravi@acme.example",
                attributes={},
            )
        )
        assert line.startswith("- Ravi (+91 98765 43210, ravi@acme.example)")
