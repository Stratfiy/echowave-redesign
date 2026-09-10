"""The bell shows what the email said, once, to the whole account.

The email log already proves a notice is not sent twice; what is guarded
here is that the inbox follows the same rule, that reading is shared across
the organization, and that one organization never sees another's.
"""

import pytest

from api.db.models import OrganizationModel, UserModel
from api.services.messaging import announce
from api.services.notifications import inbox


async def _org(session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


@pytest.mark.asyncio
class TestPosting:
    async def test_a_notice_lands_unread(self, db_session, async_session):
        org = await _org(async_session, "lands")
        assert await inbox.post(
            organization_id=org.id,
            kind="low_balance",
            dedupe_key="low:2026-09-10",
            title="Your credit is low",
            body="First line.\n\nSecond paragraph the bell does not need.",
            link="/billing",
        )
        items = await inbox.list_items(organization_id=org.id)
        assert [i.title for i in items] == ["Your credit is low"]
        assert items[0].body == "First line."
        assert items[0].read_at is None
        assert await inbox.unread_count(organization_id=org.id) == 1

    async def test_the_same_notice_posts_once(self, db_session, async_session):
        org = await _org(async_session, "once")
        first = await inbox.post(
            organization_id=org.id, kind="k", dedupe_key="d", title="t"
        )
        second = await inbox.post(
            organization_id=org.id, kind="k", dedupe_key="d", title="t"
        )
        assert (first, second) == (True, False)
        assert await inbox.unread_count(organization_id=org.id) == 1

    async def test_organizations_do_not_see_each_other(self, db_session, async_session):
        mine = await _org(async_session, "mine")
        theirs = await _org(async_session, "theirs")
        await inbox.post(organization_id=theirs.id, kind="k", dedupe_key="d", title="t")
        assert await inbox.list_items(organization_id=mine.id) == []


@pytest.mark.asyncio
class TestReading:
    async def test_reading_is_shared_across_the_organization(
        self, db_session, async_session
    ):
        org = await _org(async_session, "shared")
        await inbox.post(organization_id=org.id, kind="k", dedupe_key="1", title="a")
        await inbox.post(organization_id=org.id, kind="k", dedupe_key="2", title="b")

        assert await inbox.mark_read(organization_id=org.id) == 2
        assert await inbox.unread_count(organization_id=org.id) == 0
        # Idempotent: nothing left to mark.
        assert await inbox.mark_read(organization_id=org.id) == 0

    async def test_one_item_can_be_read_on_its_own(self, db_session, async_session):
        org = await _org(async_session, "one")
        await inbox.post(organization_id=org.id, kind="k", dedupe_key="1", title="a")
        await inbox.post(organization_id=org.id, kind="k", dedupe_key="2", title="b")
        first = (await inbox.list_items(organization_id=org.id))[-1]

        assert await inbox.mark_read(organization_id=org.id, ids=[first.id]) == 1
        assert await inbox.unread_count(organization_id=org.id) == 1


@pytest.mark.asyncio
class TestTheEmailPathFeedsTheBell:
    async def test_an_announced_notice_is_under_the_bell(
        self, db_session, async_session, monkeypatch
    ):
        """announce() claims the email row and posts the same notice in-app,
        so a caller that mails gets the bell for free."""
        org = await _org(async_session, "announced")
        user = UserModel(provider_id="user-announced", email="a@example.com")
        async_session.add(user)
        await async_session.flush()

        async def _send(**kwargs):
            from api.services.messaging.email import SendResult

            return SendResult(ok=True, error=None)

        monkeypatch.setattr(announce.email, "send_email", _send)
        await announce.announce(
            organization_id=org.id,
            kind="welcome",
            notice=announce.Notice(
                subject="Welcome",
                body="Hello.\n\nMore.",
                dedupe_key="once",
                link="/start",
            ),
            to=["a@example.com"],
        )

        items = await inbox.list_items(organization_id=org.id)
        assert [(i.title, i.body, i.link) for i in items] == [
            ("Welcome", "Hello.", "/start")
        ]


@pytest.mark.asyncio
class TestTheRoutes:
    async def test_a_member_lists_and_reads_the_inbox(
        self, db_session, async_session, test_client_factory
    ):
        from api.db.models import OrganizationMembershipModel
        from api.enums import OrganizationRole

        org = await _org(async_session, "route")
        user = UserModel(provider_id="user-route", selected_organization_id=org.id)
        async_session.add(user)
        await async_session.flush()
        async_session.add(
            OrganizationMembershipModel(
                user_id=user.id,
                organization_id=org.id,
                role=OrganizationRole.MEMBER.value,
            )
        )
        await async_session.flush()
        await inbox.post(organization_id=org.id, kind="k", dedupe_key="1", title="a")

        async with test_client_factory(user) as client:
            listed = await client.get("/api/v1/notifications")
            assert listed.status_code == 200
            assert listed.json()["unread"] == 1
            assert listed.json()["items"][0]["title"] == "a"

            read = await client.post("/api/v1/notifications/read", json={})
            assert read.status_code == 200
            assert read.json() == {"marked": 1, "unread": 0}
