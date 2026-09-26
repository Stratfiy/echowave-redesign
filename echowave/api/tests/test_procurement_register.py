"""The procurement register: gapless numbers, and one workspace's rows only.

A PO number printed on a document a vendor holds cannot be taken back, and
a gap in the series is an auditor's question. Two drafts at the same instant
must take consecutive numbers; each April the series restarts; and nothing
another workspace does can read, move or number this workspace's rows.
"""

import asyncio
from datetime import UTC, date, datetime

import pytest

from api.db.models import (
    OrganizationModel,
    ProcurementDocumentModel,
    ProcurementSeriesModel,
)
from api.services.documents import register


async def _org(session, name: str) -> int:
    org = OrganizationModel(
        provider_id=f"proc-{name}-{datetime.now(UTC).timestamp()}",
        quota_decibyl_tokens=0,
    )
    session.add(org)
    await session.flush()
    return org.id


@pytest.mark.asyncio
class TestNumbering:
    async def test_numbers_are_consecutive_and_formatted(self, async_session):
        org = await _org(async_session, "seq")
        numbers = [
            (
                await register.create(
                    async_session,
                    organization_id=org,
                    kind="purchase_order",
                    issue_date=date(2026, 9, 26),
                )
            ).number
            for _ in range(3)
        ]
        assert numbers == ["PO/26-27/0001", "PO/26-27/0002", "PO/26-27/0003"]

    async def test_each_kind_has_its_own_series(self, async_session):
        org = await _org(async_session, "kinds")
        po = await register.create(
            async_session,
            organization_id=org,
            kind="purchase_order",
            issue_date=date(2026, 9, 1),
        )
        rfq = await register.create(
            async_session, organization_id=org, kind="rfq", issue_date=date(2026, 9, 1)
        )
        assert po.number == "PO/26-27/0001"
        assert rfq.number == "RFQ/26-27/0001"
        assert rfq.series == "RFQ/26-27"

    async def test_april_restarts_the_series(self, async_session):
        org = await _org(async_session, "fy")
        march = await register.create(
            async_session,
            organization_id=org,
            kind="purchase_order",
            issue_date=date(2027, 3, 31),
        )
        april = await register.create(
            async_session,
            organization_id=org,
            kind="purchase_order",
            issue_date=date(2027, 4, 1),
        )
        assert march.number == "PO/26-27/0001"
        assert april.number == "PO/27-28/0001"

    async def test_a_workspace_prefix_is_its_own_series(self, async_session):
        org = await _org(async_session, "prefix")
        own = await register.create(
            async_session,
            organization_id=org,
            kind="purchase_order",
            issue_date=date(2026, 9, 1),
            prefix="blr-po",
        )
        default = await register.create(
            async_session,
            organization_id=org,
            kind="purchase_order",
            issue_date=date(2026, 9, 1),
        )
        assert own.number == "BLR-PO/26-27/0001"
        assert default.number == "PO/26-27/0001"

    async def test_workspaces_number_independently(self, async_session):
        a = await _org(async_session, "a")
        b = await _org(async_session, "b")
        first_a = await register.create(
            async_session,
            organization_id=a,
            kind="purchase_order",
            issue_date=date(2026, 9, 1),
        )
        first_b = await register.create(
            async_session,
            organization_id=b,
            kind="purchase_order",
            issue_date=date(2026, 9, 1),
        )
        assert first_a.number == first_b.number == "PO/26-27/0001"


@pytest.mark.asyncio
class TestConcurrentNumbering:
    """Committed sessions rather than the test savepoint: the row lock only
    means anything across real concurrent transactions, and the first draft
    of a year is the sharp case -- every transaction misses the series row."""

    async def test_simultaneous_drafts_take_distinct_consecutive_numbers(
        self, test_engine, setup_test_database
    ):
        from sqlalchemy.ext.asyncio import async_sessionmaker

        maker = async_sessionmaker(bind=test_engine, expire_on_commit=False)
        async with maker() as setup:
            org_id = await _org(setup, "concurrent")
            await setup.commit()

        async def draft() -> str:
            async with maker() as session:
                row = await register.create(
                    session,
                    organization_id=org_id,
                    kind="purchase_order",
                    issue_date=date(2098, 6, 1),
                )
                await session.commit()
                return row.number

        try:
            numbers = await asyncio.gather(*(draft() for _ in range(8)))
            serials = sorted(int(n.rsplit("/", 1)[1]) for n in numbers)
            assert len(set(numbers)) == 8, numbers
            assert serials == list(range(1, 9)), serials
        finally:
            async with maker() as cleanup:
                for model in (ProcurementDocumentModel, ProcurementSeriesModel):
                    await cleanup.execute(
                        model.__table__.delete().where(model.organization_id == org_id)
                    )
                await cleanup.execute(
                    OrganizationModel.__table__.delete().where(
                        OrganizationModel.id == org_id
                    )
                )
                await cleanup.commit()


@pytest.mark.asyncio
class TestTenantIsolation:
    async def test_another_workspace_cannot_read_move_or_list_a_row(
        self, async_session
    ):
        mine = await _org(async_session, "mine")
        theirs = await _org(async_session, "theirs")
        row = await register.create(
            async_session,
            organization_id=mine,
            kind="purchase_order",
            issue_date=date(2026, 9, 1),
            counterparty_name="Bharat Steels",
        )

        assert (
            await register.get(
                async_session, organization_id=theirs, register_id=row.id
            )
            is None
        )
        assert (
            await register.get(async_session, organization_id=theirs, number=row.number)
            is None
        )
        assert await register.list_rows(async_session, organization_id=theirs) == []
        with pytest.raises(register.RegisterError):
            await register.update(
                async_session,
                organization_id=theirs,
                register_id=row.id,
                status="cancelled",
            )
        assert row.status == "draft"
        found = await register.get(
            async_session, organization_id=mine, register_id=row.id
        )
        assert found is not None and found.counterparty_name == "Bharat Steels"


@pytest.mark.asyncio
class TestFollowUp:
    async def test_status_due_date_and_notes(self, async_session):
        org = await _org(async_session, "follow")
        row = await register.create(
            async_session,
            organization_id=org,
            kind="purchase_order",
            issue_date=date(2026, 9, 1),
            due_date=date(2026, 9, 10),
            status="issued",
        )
        await register.update(
            async_session,
            organization_id=org,
            number=row.number.lower(),
            status="part_delivered",
            due_date="15-10-2026",
            note="60 of 100 bags received on GRN 118",
        )
        assert row.status == "part_delivered"
        assert row.due_date == date(2026, 10, 15)
        assert row.data["notes"][-1]["note"] == "60 of 100 bags received on GRN 118"

    async def test_an_unknown_status_is_refused(self, async_session):
        org = await _org(async_session, "bad")
        row = await register.create(
            async_session, organization_id=org, kind="rfq", issue_date=date(2026, 9, 1)
        )
        with pytest.raises(register.RegisterError, match="status"):
            await register.update(
                async_session, organization_id=org, register_id=row.id, status="shipped"
            )

    async def test_list_filters(self, async_session):
        org = await _org(async_session, "list")
        early = await register.create(
            async_session,
            organization_id=org,
            kind="purchase_order",
            issue_date=date(2026, 9, 1),
            due_date=date(2026, 9, 5),
            status="issued",
        )
        await register.create(
            async_session,
            organization_id=org,
            kind="purchase_order",
            issue_date=date(2026, 9, 1),
            due_date=date(2026, 12, 5),
            status="issued",
        )
        await register.create(
            async_session, organization_id=org, kind="rfq", issue_date=date(2026, 9, 1)
        )
        rows = await register.list_rows(
            async_session,
            organization_id=org,
            kind="purchase_order",
            status="issued",
            due_before="2026-10-01",
        )
        assert [r.id for r in rows] == [early.id]
        assert register.summary(early)["overdue"] is True
