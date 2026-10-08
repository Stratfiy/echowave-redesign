"""Stream `reach`: a person's connections to outside servers.

Done when: a person connects an outside tool (pasted token, or a sign-in on
the server's own screen) and its tools are read and classified; an address
inside our network is refused; the connection is theirs alone -- a
colleague in the same workspace, and anyone in another, can neither list,
use nor revoke it; and no route ever returns a token.
"""

from __future__ import annotations

import httpx
import pytest

from api import constants
from api.services.reach import connections, oauth, safety, vault
from api.tests.support import reach_fakes
from api.tests.support.reach_fixtures import (  # noqa: F401
    client_as,
    connect_notes,
    connect_zomato,
    fake_servers,
    people,
    reach_on,
)

pytestmark = pytest.mark.asyncio


class TestConnecting:
    async def test_a_pasted_token_connects_and_reads_the_tools(self, reach_on, people):
        row = await connect_notes(people.org, people.a.id, reach_on.base)
        assert row.status == "connected" and row.auth == "token"
        tools = {t["name"]: t["read"] for t in row.tools}
        assert tools == {
            "search_notes": True,
            "create_note": False,
            # Named like a read; its own description says it sends.
            "get_and_share_digest": False,
        }

    async def test_the_token_is_stored_encrypted(self, reach_on, people):
        row = await connect_notes(people.org, people.a.id, reach_on.base)
        assert reach_fakes.NOTES_TOKEN not in (row.secret_encrypted or "")
        assert vault.open_(row.secret_encrypted)["token"] == reach_fakes.NOTES_TOKEN

    async def test_a_wrong_token_is_refused_in_words(self, reach_on, people):
        with pytest.raises(connections.ConnectError, match="not accepted"):
            await connect_notes(people.org, people.a.id, reach_on.base, token="wrong")

    async def test_an_address_nothing_answers_is_refused_in_words(
        self, reach_on, people
    ):
        """What the chip shows the person: words, never an exception's class."""
        nobody = f"http://127.0.0.1:{reach_fakes.free_port()}/mcp"
        with pytest.raises(connections.ConnectError) as refused:
            await connect_notes(people.org, people.a.id, nobody.rsplit("/mcp", 1)[0])
        assert "could not be reached" in str(refused.value)
        assert "Error" not in str(refused.value), str(refused.value)

    async def test_a_sign_in_server_starts_a_sign_in(self, reach_on, people):
        from api.services.reach.ordering import providers

        started = await connections.connect(
            organization_id=people.org,
            user_id=people.a.id,
            kind="ordering",
            provider="zomato",
            name="Zomato",
            server_url=providers.ZOMATO.url(),
        )
        assert started.connection.status == "pending"
        url = httpx.URL(started.authorize_url)
        assert url.params["code_challenge_method"] == "S256"
        assert url.params["redirect_uri"] == oauth.redirect_uri()
        assert url.params["state"]
        # Only the hash of the state is kept.
        assert started.connection.oauth_state_hash == oauth.state_hash(
            url.params["state"]
        )

    async def test_the_callback_finishes_it(self, reach_on, people):
        row = await connect_zomato(people.org, people.a.id)
        assert row.status == "connected" and row.auth == "oauth"
        assert {"create_cart", "checkout_cart"} <= {t["name"] for t in row.tools}
        assert row.oauth_state_hash is None

    async def test_a_state_works_once(self, reach_on, people):
        from api.services.reach.ordering import providers

        started = await connections.connect(
            organization_id=people.org,
            user_id=people.a.id,
            kind="ordering",
            provider="zomato",
            name="Zomato",
            server_url=providers.ZOMATO.url(),
        )
        async with httpx.AsyncClient() as client:
            redirect = await client.get(started.authorize_url, follow_redirects=False)
        location = httpx.URL(redirect.headers["location"])
        await connections.finish_sign_in(
            location.params["state"], location.params["code"]
        )
        with pytest.raises(connections.ConnectError, match="already been used"):
            await connections.finish_sign_in(
                location.params["state"], location.params["code"]
            )

    async def test_a_forged_state_finds_nothing(self, reach_on, people):
        with pytest.raises(connections.ConnectError):
            await connections.finish_sign_in("forged", "code")

    async def test_connecting_again_replaces(self, reach_on, people):
        first = await connect_notes(people.org, people.a.id, reach_on.base)
        second = await connect_notes(people.org, people.a.id, reach_on.base)
        mine = await connections.mine(people.org, people.a.id)
        assert [r.uuid for r in mine] == [second.uuid]
        assert await connections.get(people.org, people.a.id, first.uuid) is None


class TestAddresses:
    async def test_a_private_address_is_refused_in_production(
        self, reach_on, monkeypatch
    ):
        monkeypatch.setattr(constants, "ENVIRONMENT", "production")
        with pytest.raises(safety.UnsafeAddress):
            await safety.check_address("https://127.0.0.1/mcp")

    async def test_a_private_address_is_refused_without_the_switch(self, monkeypatch):
        monkeypatch.setattr(constants, "REACH_ALLOW_PRIVATE_SERVERS", False)
        for url in (
            "https://127.0.0.1/mcp",
            "https://169.254.169.254/latest/meta-data",
            "https://10.0.0.5/mcp",
            "http://example.com/mcp",
            "https://user:pass@example.com/mcp",
            "file:///etc/passwd",
        ):
            with pytest.raises(safety.UnsafeAddress):
                await safety.check_address(url)

    async def test_a_public_https_address_passes(self, monkeypatch):
        monkeypatch.setattr(constants, "REACH_ALLOW_PRIVATE_SERVERS", False)

        async def public(*args, **kwargs):
            return [(2, 1, 6, "", ("93.184.216.34", 443))]

        import asyncio

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", public)
        assert await safety.check_address("https://tools.example.com/mcp")


class TestTheirsAlone:
    async def test_a_colleague_cannot_see_use_or_revoke_it(self, reach_on, people):
        row = await connect_notes(people.org, people.a.id, reach_on.base)
        assert await connections.mine(people.org, people.b.id) == []
        assert await connections.get(people.org, people.b.id, row.uuid) is None
        assert await connections.revoke(people.org, people.b.id, row.uuid) is False
        assert (
            await connections.get(people.org, people.a.id, row.uuid)
        ).status == "connected"

    async def test_another_workspace_cannot_either(self, reach_on, people):
        row = await connect_notes(people.org, people.a.id, reach_on.base)
        # The same person, in another workspace, does not carry it there.
        assert await connections.get(people.other, people.a.id, row.uuid) is None
        assert await connections.mine(people.other, people.c.id) == []

    async def test_routes_list_only_the_callers(self, reach_on, people):
        row = await connect_notes(people.org, people.a.id, reach_on.base)
        async with client_as(people.a) as client:
            mine = (await client.get("/api/v1/reach/connections")).json()["connections"]
        assert [c["id"] for c in mine] == [row.uuid]
        async with client_as(people.b) as client:
            theirs = (await client.get("/api/v1/reach/connections")).json()[
                "connections"
            ]
            assert theirs == []
            assert (
                await client.delete(f"/api/v1/reach/connections/{row.uuid}")
            ).status_code == 404
            assert (
                await client.post(f"/api/v1/reach/connections/{row.uuid}/refresh")
            ).status_code == 404

    async def test_no_route_returns_a_secret(self, reach_on, people):
        async with client_as(people.a) as client:
            response = await client.post(
                "/api/v1/reach/connections",
                json={
                    "kind": "tool",
                    "name": "Notes",
                    "server_url": f"{reach_on.base}/notes/mcp",
                    "token": reach_fakes.NOTES_TOKEN,
                },
            )
            assert response.status_code == 200
            assert reach_fakes.NOTES_TOKEN not in response.text
            listed = await client.get("/api/v1/reach/connections")
            assert reach_fakes.NOTES_TOKEN not in listed.text
            assert "secret" not in listed.text

    async def test_disconnecting_deletes_the_token(self, reach_on, people):
        row = await connect_notes(people.org, people.a.id, reach_on.base)
        async with client_as(people.a) as client:
            assert (
                await client.delete(f"/api/v1/reach/connections/{row.uuid}")
            ).status_code == 204
        from sqlalchemy import select

        from api.db import db_client
        from api.db.reach_models import ReachConnectionModel

        async with db_client.async_session() as session:
            stored = await session.scalar(
                select(ReachConnectionModel).where(ReachConnectionModel.id == row.id)
            )
        assert stored.revoked_at is not None and stored.secret_encrypted is None


class TestRoutes:
    async def test_a_tool_needs_its_flag(self, reach_on, monkeypatch, people):
        monkeypatch.setattr(constants, "OUTSIDE_TOOLS_ENABLED", False)
        async with client_as(people.a) as client:
            response = await client.post(
                "/api/v1/reach/connections",
                json={
                    "kind": "tool",
                    "name": "Notes",
                    "server_url": f"{reach_on.base}/notes/mcp",
                },
            )
        assert response.status_code == 404

    async def test_an_app_that_needs_setup_says_so(self, reach_on, people):
        async with client_as(people.a) as client:
            response = await client.post(
                "/api/v1/reach/connections",
                json={"kind": "ordering", "provider": "swiggy"},
            )
            assert response.status_code == 409
            assert "Builders Club" in response.json()["detail"]
            states = {
                p["provider"]: p["state"]
                for p in (await client.get("/api/v1/reach/providers")).json()[
                    "providers"
                ]
            }
        assert states == {"zomato": "available", "swiggy": "needs_setup"}

    async def test_the_callback_page_says_connected(self, reach_on, people):
        from api.services.reach.ordering import providers

        started = await connections.connect(
            organization_id=people.org,
            user_id=people.a.id,
            kind="ordering",
            provider="zomato",
            name="Zomato",
            server_url=providers.ZOMATO.url(),
        )
        async with httpx.AsyncClient() as fake:
            redirect = await fake.get(started.authorize_url, follow_redirects=False)
        location = httpx.URL(redirect.headers["location"])
        async with client_as(people.b) as client:
            page = await client.get(
                "/api/v1/reach/oauth/callback",
                params={
                    "state": location.params["state"],
                    "code": location.params["code"],
                },
            )
        assert page.status_code == 200 and "Zomato is connected" in page.text
        # Connected for the person who started it, whoever's browser came back.
        assert (
            await connections.live(people.org, people.a.id, "ordering", "zomato")
        ).status == "connected"
        assert (
            await connections.live(people.org, people.b.id, "ordering", "zomato")
            is None
        )
