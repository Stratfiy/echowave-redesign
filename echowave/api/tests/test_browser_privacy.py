"""A person's browser is theirs: nobody else sees it, drives it or reuses
what it kept.

"Nobody else" includes a colleague in the same workspace reading the same
Decibyl thread -- they see that a browser ran (the panel row carries only an
id), and nothing of what it saw. And a saved login is lent only to the
person who saved it, encrypted at rest, gone when they delete it.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from api import constants
from api.db import db_client
from api.services.browser import cookies, session
from api.tests.support.browser import (
    BILL_URL,
    SITE,
    FakeDriver,
    Page,
    Step,
    account,
    bill_page,
    browser_on,  # noqa: F401
    colleague,
    run,
    serial_db,  # noqa: F401
    start,
    until,
)

SESSION_COOKIE = {
    "name": "session",
    "value": "s3cr3t-session-token",
    "domain": f".{SITE}",
    "path": "/",
    "secure": True,
    "httpOnly": True,
}


class TestNobodyElseSeesIt:
    async def test_a_colleague_and_a_stranger_get_nothing_of_a_session(
        self, browser_on, async_session, test_client_factory
    ):
        org, owner = await account(async_session, "priv-owner")
        mate = await colleague(async_session, org, "priv-mate")
        _, stranger = await account(async_session, "priv-stranger")
        row = await start(org, owner, request=f"Check my bill on {SITE}")
        base = f"/api/v1/browser/sessions/{row.session_uuid}"
        for person in (mate, stranger):
            async with test_client_factory(person) as client:
                assert (await client.get(base)).status_code == 404
                assert (await client.get(f"{base}/screen")).status_code == 404
                assert (await client.post(f"{base}/takeover")).status_code == 404
                assert (
                    await client.post(
                        f"{base}/input", json={"kind": "type", "text": "x"}
                    )
                ).status_code == 404
                assert (await client.post(f"{base}/stop")).status_code == 404
        async with test_client_factory(owner) as client:
            assert (await client.get(base)).status_code == 200

    async def test_the_thread_row_carries_an_id_and_nothing_seen(
        self, browser_on, async_session
    ):
        org, owner = await account(async_session, "priv-row")
        row = await start(org, owner, request=f"Check my bill on {SITE}")
        fake = FakeDriver(
            pages={BILL_URL: Page(BILL_URL, bill_page("<p>Account 99887766</p>"))},
            plan=[Step("done", text="Done.")],
        )
        await run(row, fake)
        panel = await db_client.get_agent_event(row.event_id, organization_id=org.id)
        assert "99887766" not in json.dumps(panel.payload)
        assert set(panel.payload) == {"from", "session_uuid", "by", "start_url"}


class TestLogins:
    @pytest.fixture(autouse=True)
    def _a_credential_key(self, monkeypatch):
        """Logins are kept only under the platform key, and CI sets none. A
        fixed test key, so these run the same everywhere; the test that needs
        the key absent sets it to None itself, after this."""
        monkeypatch.setattr(
            constants,
            "PLATFORM_CREDENTIAL_SECRET",
            "aW1hLXRlc3Qta2V5LW5vdC1hLXJlYWwtc2VjcmV0MDA=",
        )

    async def test_kept_cookies_are_encrypted_and_lent_only_to_their_owner(
        self, browser_on, async_session, test_client_factory
    ):
        org, owner = await account(async_session, "priv-cookie-owner")
        mate = await colleague(async_session, org, "priv-cookie-mate")
        await cookies.save(
            organization_id=org.id,
            user_id=owner.id,
            site=SITE,
            cookies=[SESSION_COOKIE],
        )
        (saved,) = await db_client.list_browser_logins(
            organization_id=org.id, user_id=owner.id
        )
        assert b"s3cr3t-session-token" not in bytes(saved.cookies_encrypted)
        assert (
            cookies.decrypt(saved.cookies_encrypted)[0]["value"]
            == "s3cr3t-session-token"
        )

        # The owner's next browser on that site is signed in...
        fake = FakeDriver(
            pages={BILL_URL: Page(BILL_URL, bill_page())},
            plan=[Step("done", text="ok")],
        )
        mine = await start(org, owner, request=f"Check my bill on {SITE}")
        await run(mine, fake)
        assert (
            fake.boxes["fake-1"].spec["cookies"][0]["value"] == "s3cr3t-session-token"
        )

        # ...and a colleague's on the same site is not.
        fake2 = FakeDriver(
            pages={BILL_URL: Page(BILL_URL, bill_page())},
            plan=[Step("done", text="ok")],
        )
        theirs = await start(org, mate, request=f"Check my bill on {SITE}")
        await run(theirs, fake2)
        assert fake2.boxes["fake-1"].spec["cookies"] == []

        async with test_client_factory(mate) as client:
            assert (await client.get("/api/v1/browser/logins")).json()["logins"] == []
            assert (
                await client.delete(f"/api/v1/browser/logins/{saved.id}")
            ).status_code == 404
        async with test_client_factory(owner) as client:
            listed = (await client.get("/api/v1/browser/logins")).json()
            assert [lg["site"] for lg in listed["logins"]] == [SITE]
            assert listed["can_keep_logins"] is True
            assert (
                await client.delete(f"/api/v1/browser/logins/{saved.id}")
            ).status_code == 200
        assert (
            await db_client.list_browser_logins(
                organization_id=org.id, user_id=owner.id
            )
            == []
        )

    async def test_a_task_with_no_sites_borrows_no_login(
        self, browser_on, async_session
    ):
        org, owner = await account(async_session, "priv-cookie-nosite")
        await cookies.save(
            organization_id=org.id,
            user_id=owner.id,
            site=SITE,
            cookies=[SESSION_COOKIE],
        )
        loaded, used = await cookies.load(
            organization_id=org.id, user_id=owner.id, task_sites=[]
        )
        assert loaded == [] and used == []

    async def test_only_the_site_asked_for_is_kept_on_hand_back(
        self, browser_on, async_session
    ):
        """Keep me signed in, said on hand back, keeps that site's cookies --
        not the tracker's that rode along."""
        org, owner = await account(async_session, "priv-keep")
        tracker = {
            "name": "t",
            "value": "track-me",
            "domain": ".ads.example",
            "path": "/",
        }
        page = Page(
            BILL_URL,
            bill_page('<a id="view" href="/bill">View bill</a>'),
            sets_cookies=[SESSION_COOKIE, tracker],
        )
        fake = FakeDriver(
            pages={BILL_URL: page},
            plan=[Step("click", "view"), Step("done", text="ok")],
            captcha_on=BILL_URL,
        )
        row = await start(org, owner, request=f"Check my bill on {SITE}")
        from api.services.browser import drivers

        drivers.use(fake)
        job = asyncio.create_task(session.run(row.session_uuid, BILL_URL))

        async def captcha():
            return (
                await db_client.get_browser_session_for_worker(row.session_uuid)
            ).state == "captcha"

        await until(captcha)
        current = await db_client.get_browser_session_for_worker(row.session_uuid)
        await session.person_command(current, {"cmd": "takeover"})
        await until(lambda: fake.boxes["fake-1"].taken_over.is_set())
        current = await db_client.get_browser_session_for_worker(row.session_uuid)
        await until(lambda: _state_is(row, "taken_over"))
        current = await db_client.get_browser_session_for_worker(row.session_uuid)
        await session.person_command(current, {"cmd": "handback", "keep_login": True})
        await asyncio.wait_for(job, timeout=20)

        (saved,) = await db_client.list_browser_logins(
            organization_id=org.id, user_id=owner.id
        )
        kept = cookies.decrypt(saved.cookies_encrypted)
        assert saved.site == SITE
        assert [c["name"] for c in kept] == ["session"]
        row = await db_client.get_browser_session_for_worker(row.session_uuid)
        assert row.receipt["kept_logins"] == [SITE]

    async def test_without_a_key_nothing_is_kept_in_plaintext(
        self, browser_on, monkeypatch, async_session
    ):
        monkeypatch.setattr(constants, "PLATFORM_CREDENTIAL_SECRET", None)
        org, owner = await account(async_session, "priv-nokey")
        assert cookies.can_keep() is False
        with pytest.raises(cookies.LoginsUnavailable):
            await cookies.save(
                organization_id=org.id,
                user_id=owner.id,
                site=SITE,
                cookies=[SESSION_COOKIE],
            )
        assert (
            await db_client.list_browser_logins(
                organization_id=org.id, user_id=owner.id
            )
            == []
        )


async def _state_is(row, wanted):
    current = await db_client.get_browser_session_for_worker(row.session_uuid)
    return current.state == wanted
