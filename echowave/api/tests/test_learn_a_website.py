"""Learn a website: paste the address, every agent knows the business.

A clinic's site is served from a stub transport; everything else is real --
the link ranking, the robots rules, the file written to Files, the document
row and its scope, the version history on a second learn, and the list of
documents an agent's run reads. Only the object store and the job queue are
stubbed, and both are checked for what they were handed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest

from api import constants
from api.db import db_client
from api.db.models import (
    OrganizationMembershipModel,
    OrganizationModel,
    UserModel,
)
from api.services import storage
from api.services.knowledge_base import website
from api.services.workflow import decibyl

HOME = """<html><head><title>Lotus Dental Care</title></head><body>
<nav><a href="/">Home</a> <a href="/about-us">About us</a>
<a href="/contact">Contact</a> <a href="/blog/2019/old-post">Old post</a>
<a href="/treatments/">Treatments</a> <a href="/login">Login</a>
<a href="/brochure.pdf">Brochure</a> <a href="https://facebook.com/lotus">FB</a>
<a href="mailto:hi@lotusdental.example">Mail</a> <a href="/private">Staff</a></nav>
<h1>Lotus Dental Care, Pune</h1>
<p>Gentle dentistry for the whole family since 2009.</p>
<footer>Copyright Lotus Dental Care</footer>
<script>var tracking = "not text";</script>
</body></html>"""

CONTACT = """<html><head><title>Contact - Lotus Dental Care</title></head><body>
<nav><a href="/">Home</a> <a href="/about-us">About us</a></nav>
<h1>Contact</h1><p>Call +91 98220 12345 or write to hello@lotusdental.example</p>
<p>Open Monday to Saturday, 9am to 8pm. Closed on Sundays.</p>
<footer>Copyright Lotus Dental Care</footer></body></html>"""

TREATMENTS = """<html><head><title>Treatments</title></head><body>
<nav><a href="/">Home</a></nav>
<p>Cleaning: Rs 800. Root canal: from Rs 4,500. Braces: from Rs 35,000.</p>
<footer>Copyright Lotus Dental Care</footer></body></html>"""

ABOUT = """<html><head><title>About us</title></head><body>
<p>Dr Asha Rao leads a team of four dentists.</p></body></html>"""

ROBOTS = "User-agent: *\nDisallow: /private\n"


def _site(host: str, pages: dict[str, str], *, robots: str = ROBOTS, asked=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if asked is not None:
            asked.append(request.url.path)
        if request.url.host not in (host, f"www.{host}"):
            return httpx.Response(404)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        body = pages.get(request.url.path.rstrip("/") or "/")
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )


def _clinic(host: str, **kw):
    return _site(
        host,
        {"/": HOME, "/contact": CONTACT, "/treatments": TREATMENTS, "/about-us": ABOUT},
        **kw,
    )


@pytest.fixture
def stored(monkeypatch):
    """The object store, as a dict; and the jobs queued, as a list."""
    files: dict[str, bytes] = {}
    jobs: list[tuple] = []

    async def put(key, data):
        files[key] = data
        return True

    monkeypatch.setattr(
        storage, "storage_fs", SimpleNamespace(acreate_file_from_bytes=put)
    )

    async def enqueue(name, *args):
        jobs.append((name, *args))

    with patch("api.tasks.arq.enqueue_job", enqueue):
        yield SimpleNamespace(files=files, jobs=jobs)


async def _member(async_session):
    slug = uuid4().hex[:10]
    org = OrganizationModel(provider_id=f"site-{slug}")
    async_session.add(org)
    await async_session.flush()
    user = UserModel(provider_id=f"user-{slug}", selected_organization_id=org.id)
    async_session.add(user)
    await async_session.flush()
    async_session.add(
        OrganizationMembershipModel(organization_id=org.id, user_id=user.id)
    )
    await async_session.commit()
    return org, user


class TestWhichPagesAreRead:
    def test_customers_questions_come_first_and_junk_never(self):
        site = "lotusdental.example"
        links = website.links_of(f"https://{site}/", HOME, site)
        urls = [u for u, _ in links]
        assert f"https://{site}/contact" in urls
        assert f"https://{site}/treatments" in urls
        # Files, account pages, other sites and mail links are not pages.
        assert not any(u.endswith(".pdf") for u in urls)
        assert not any("login" in u for u in urls)
        assert not any("facebook" in u for u in urls)
        order = website.ranked(links, skip={f"https://{site}/"})
        assert order.index(f"https://{site}/contact") < order.index(
            f"https://{site}/blog/2019/old-post"
        )
        assert order.index(f"https://{site}/treatments") < order.index(
            f"https://{site}/blog/2019/old-post"
        )
        assert f"https://{site}/" not in order

    @pytest.mark.asyncio
    async def test_a_site_is_read_with_its_robots_rules_and_says_what_it_skipped(
        self,
    ):
        host = f"lotus-{uuid4().hex[:6]}.example"
        asked: list[str] = []
        site = await website.read_site(
            f"https://{host}/", client=_clinic(host, asked=asked)
        )
        titles = [p.title for p in site.pages]
        assert titles[0] == "Lotus Dental Care"
        assert "Contact - Lotus Dental Care" in titles
        assert "Treatments" in titles
        # robots.txt says no to /private: never fetched, and said so.
        assert "/private" not in asked
        assert any(url.endswith("/private") for url, _ in site.skipped)
        # A page that does not exist is skipped with its reason, not lost.
        assert any("404" in why for _, why in site.skipped)
        assert len(site.pages) <= website.MAX_PAGES

    @pytest.mark.asyncio
    async def test_an_address_written_without_https_is_read(self):
        host = f"lotus-{uuid4().hex[:6]}.example"
        site = await website.read_site(host, client=_clinic(host))
        assert site.url == f"https://{host}"
        assert site.pages[0].title == "Lotus Dental Care"

    @pytest.mark.asyncio
    async def test_the_social_networks_and_private_addresses_are_refused(self):
        for url in ("https://facebook.com/lotus", "http://127.0.0.1/admin"):
            with pytest.raises(website.SiteRefused):
                await website.read_site(url, client=_clinic("x.example"))


class TestTheFile:
    @pytest.mark.asyncio
    async def test_one_file_with_each_page_once_and_the_menu_once(self):
        host = f"lotus-{uuid4().hex[:6]}.example"
        site = await website.read_site(f"https://{host}/", client=_clinic(host))
        text = website.as_markdown(site)
        assert text.startswith("# Lotus Dental Care")
        assert f"Website: https://{host}/" in text
        assert "Open Monday to Saturday, 9am to 8pm" in text
        assert "Root canal: from Rs 4,500" in text
        assert f"Page: https://{host}/contact" in text
        # The footer is said once, not once per page; script is not text.
        assert text.count("Copyright Lotus Dental Care") == 1
        assert "tracking" not in text
        found = website.found_in(site)
        assert "hello@lotusdental.example" in found["emails"]


@pytest.mark.asyncio
class TestLearning:
    async def test_every_agent_answers_from_it_and_learning_again_updates_it(
        self, db_session, async_session, stored
    ):
        org, user = await _member(async_session)
        host = f"lotus-{uuid4().hex[:6]}.example"

        result = await website.learn_for_thread(
            org.id,
            {"url": f"https://{host}"},
            ref_id="test:learn:1",
            author_id=user.id,
            client=_clinic(host),
        )
        assert result["status"] == "success", result
        assert result["for_agents"] is True
        assert result["file"]["new"] is True
        assert "9am to 8pm" in result["what_it_says"]
        assert "+91 98220 12345" in " ".join(result["found"]["phones"])

        document = await db_client.get_document_by_uuid(
            result["file"]["document_uuid"], organization_id=org.id
        )
        assert document.scope == "org"
        assert document.filename == f"Website - {host}.md"
        assert document.created_by == user.id
        assert document.custom_metadata["source"] == "website"
        key = document.custom_metadata["s3_key"]
        assert b"Root canal" in stored.files[key]
        # Queued to be read like any upload, under the asking member.
        _name, document_id, queued_key, org_id, provider_id, *_ = stored.jobs[0]
        assert (document_id, queued_key, org_id) == (document.id, key, org.id)
        assert provider_id == user.provider_id

        # Once read, it is company knowledge: in every agent's run.
        await db_client.update_document_status(document.id, "completed")
        assert document.document_uuid in await db_client.scoped_document_uuids(
            org.id, workflow_id=None, folder_id=None
        )

        again = await website.learn_for_thread(
            org.id,
            {"url": f"https://{host}/"},
            ref_id="test:learn:2",
            author_id=user.id,
            client=_clinic(host),
        )
        assert again["status"] == "success"
        assert again["file"]["new"] is False
        assert again["file"]["document_uuid"] == document.document_uuid
        refreshed = await db_client.get_document_by_uuid(
            document.document_uuid, organization_id=org.id
        )
        assert len(refreshed.custom_metadata["versions"]) == 2
        assert refreshed.custom_metadata["s3_key"] != key

    async def test_kept_for_the_person_it_is_not_every_agents(
        self, db_session, async_session, stored
    ):
        org, user = await _member(async_session)
        host = f"recipes-{uuid4().hex[:6]}.example"
        result = await website.learn_for_thread(
            org.id,
            {"url": f"https://{host}/", "for_agents": False},
            ref_id="test:learn:3",
            author_id=user.id,
            client=_clinic(host),
        )
        assert result["status"] == "success"
        document = await db_client.get_document_by_uuid(
            result["file"]["document_uuid"], organization_id=org.id
        )
        assert document.scope == "library"

    async def test_a_site_with_no_text_saves_nothing_and_says_why(
        self, db_session, async_session, stored
    ):
        org, user = await _member(async_session)
        host = f"empty-{uuid4().hex[:6]}.example"
        empty = "<html><body><script>render()</script></body></html>"
        result = await website.learn_for_thread(
            org.id,
            {"url": f"https://{host}/"},
            ref_id="test:learn:4",
            author_id=user.id,
            client=_site(host, {"/": empty}),
        )
        assert result["status"] == "error"
        assert "Nothing was saved" in result["error"]
        assert stored.files == {}

    async def test_a_site_that_is_down_is_refused_in_words(self, stored):
        host = f"down-{uuid4().hex[:6]}.example"
        result = await website.learn_for_thread(
            1,
            {"url": f"https://{host}/"},
            ref_id="test:learn:5",
            client=_site(host, {}),
        )
        assert result["status"] == "refused"
        assert "404" in result["reason"]


class TestDecibylHasIt:
    def test_offered_and_in_the_rules(self):
        assert website.TOOL_NAME in {t["name"] for t in decibyl.office_tools()}
        assert website.TOOL_NAME in decibyl.SYSTEM
        assert "for_agents" in decibyl.SYSTEM

    def test_the_kill_switch_takes_it_away(self, monkeypatch):
        monkeypatch.setattr(constants, "LEARN_WEBSITE_ENABLED", False)
        assert website.TOOL_NAME not in {t["name"] for t in decibyl.office_tools()}

    def test_a_learn_keeps_the_tools_so_it_can_build_the_agent_next(self):
        call = SimpleNamespace(name=website.TOOL_NAME)
        assert decibyl._was_a_read(call, {"status": "success"})
        assert decibyl._was_a_read(call, {"status": "refused"})
