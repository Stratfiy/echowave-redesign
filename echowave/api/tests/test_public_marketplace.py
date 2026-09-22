"""The marketplace a stranger can browse before signing up.

What must show: every listed role, searchable, and in full -- what it asks,
which apps it needs, what it will never do, how its work is laid out, how
often it runs. What must not: an unlisted role, the prompt text (the
founder's decision, not a default), and anything that depends on who is
asking.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import public_marketplace
from api.services.packs import resolve_listed_packs, resolve_packs

app = FastAPI()
app.include_router(public_marketplace.router, prefix="/api/v1")
client = TestClient(app, raise_server_exceptions=False)


def _listed():
    return asyncio.run(resolve_listed_packs())


class TestTheShelf:
    def test_it_opens_with_no_account(self):
        r = client.get("/api/v1/public/marketplace")
        assert r.status_code == 200
        slugs = {p["slug"] for p in r.json()["packs"]}
        assert slugs == {p.slug for p in _listed()}
        assert slugs, "the shelf should have something on it"

    def test_search_narrows_it(self):
        r = client.get("/api/v1/public/marketplace", params={"q": "prospects"})
        assert r.status_code == 200
        assert "outbound_prospecting" in {p["slug"] for p in r.json()["packs"]}


class TestOneRole:
    def test_a_role_in_full(self):
        r = client.get("/api/v1/public/marketplace/outbound_prospecting")
        assert r.status_code == 200
        body = r.json()
        assert body["card"]["name"]
        assert body["guardrails"]
        assert any(step["facts"] for step in body["steps"])
        assert body["runs"]
        assert body["speaks"] is False
        kinds = [s["kind"] for s in body["outline"]]
        assert kinds[0] == "start" and kinds[-1] == "finish"
        assert body["outline"][0]["name"] == "Find prospects"
        assert body["template_id"] == "outbound_prospecting"

    def test_the_prompt_text_is_not_published(self):
        # A page once public is cached and indexed. Publishing prompts is a
        # decision, not a default.
        from api.services.packs import resolve_pack

        pack = asyncio.run(resolve_pack("outbound_prospecting"))
        body = client.get("/api/v1/public/marketplace/outbound_prospecting").text
        for node in pack.template.nodes:
            if node.prompt and len(node.prompt) > 40:
                assert node.prompt[:40] not in body, node.name

    def test_an_unlisted_role_does_not_exist_from_outside(self):
        unlisted = [p for p in asyncio.run(resolve_packs()) if not p.listed]
        if unlisted:
            r = client.get(f"/api/v1/public/marketplace/{unlisted[0].slug}")
            assert r.status_code == 404
        assert client.get("/api/v1/public/marketplace/no-such-role").status_code == 404


def test_nothing_on_this_router_asks_who_is_calling():
    # A dependency that reads a user would turn a public page into a login
    # prompt, or worse, into something that varies by account.
    for route in public_marketplace.router.routes:
        names = {d.call.__name__ for d in route.dependant.dependencies}
        assert not names & {"get_user", "get_superuser", "get_user_optional"}, (
            route.path
        )


def test_it_is_mounted_on_the_app():
    from api.routes.main import router

    assert any(r.path == "/public/marketplace" for r in router.routes)
