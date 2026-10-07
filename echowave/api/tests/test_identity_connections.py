"""Connected apps and channels per person (screen 22; launch stream identity).

Done when: a person sees their own connections and the workspace's and never
a colleague's; consent is recorded before the app's sign-in with what was
shown and where to return; wrong-workspace sign-ins are refused;
disconnecting is an action card only its owner can approve, which revokes
at the app and blocks future use; a channel is "available" only once a
verified message arrived on it; and a failed read is never an empty list.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api import constants
from api.db import db_client
from api.enums import AgentEventKind
from api.services.identity import cards, channel_health, connections
from api.services.workflow import actions
from api.tests.identity_support import (  # noqa: F401 - fixtures
    client_as,
    composio,
    flags,
    no_queue,
    team,
)


@pytest.fixture
def on(monkeypatch):
    flags(
        monkeypatch,
        "IDENTITY_CONNECTIONS_ENABLED",
        "CONNECTIONS_PER_PERSON_ENABLED",
        "TASK_LEDGER_ENABLED",
    )


@pytest.mark.asyncio
class TestArrival:
    async def test_off_every_route_is_not_there(self, team):
        async with client_as(team.as_user(team.member)) as c:
            assert (await c.get("/api/v1/me/connections")).status_code == 404
            assert (
                await c.post("/api/v1/me/connections/start", json={"toolkit": "gmail"})
            ).status_code == 404
            assert (await c.get("/api/v1/me/identity-cards")).status_code == 404

    async def test_on_the_screen_lists_mine_and_the_workspaces(
        self, team, on, composio
    ):
        composio.add(team.org, team.member.id, "gmail", "ca_mine1")
        composio.add(team.org, None, "slack", "ca_work1")
        async with client_as(team.as_user(team.member)) as c:
            body = (await c.get("/api/v1/me/connections")).json()
        apps = {i["id"]: i for i in body["apps"]["items"]}
        assert body["apps"]["state"] == "ok"
        assert (
            apps["ca_mine1"]["owner"] == "you" and apps["ca_mine1"]["state"] == "ready"
        )
        assert apps["ca_work1"]["owner"] == "workspace"
        # A member can disconnect their own, not the workspace's.
        assert apps["ca_mine1"]["can_disconnect"] is True
        assert apps["ca_work1"]["can_disconnect"] is False
        # What it grants is listed (handoff 22: detail lists granted access).
        assert any("asks you first" in line for line in apps["ca_mine1"]["access"])
        assert {c["channel"] for c in body["channels"]} == {
            "whatsapp",
            "telegram",
            "slack",
            "teams",
        }


@pytest.mark.asyncio
class TestNobodyElses:
    async def test_a_colleagues_tenant_is_never_asked(self, team, on, composio):
        composio.add(team.org, team.owner.id, "gmail", "ca_owner")
        composio.add(team.org, team.member.id, "gmail", "ca_member")
        async with client_as(team.as_user(team.member)) as c:
            body = (await c.get("/api/v1/me/connections")).json()
        ids = {i["id"] for i in body["apps"]["items"]}
        assert "ca_member" in ids and "ca_owner" not in ids
        assert f"decibyl_org_{team.org}_user_{team.owner.id}" not in composio.asked

    async def test_a_colleague_cannot_finish_or_see_my_consent(
        self, team, on, composio
    ):
        started = await connections.start(
            organization_id=team.org,
            user_id=team.member.id,
            toolkit="gmail",
            scope="mine",
            purpose="Reply to Ravi",
            return_to="/overview?draft=1",
            is_admin=False,
        )
        async with client_as(team.as_user(team.owner)) as c:
            r = await c.post(
                f"/api/v1/me/connections/consents/{started['consent_id']}/complete"
            )
        assert r.status_code == 404

    async def test_another_members_cards_are_not_in_my_list(
        self, team, on, composio, no_queue
    ):
        composio.add(team.org, team.member.id, "gmail", "ca_member")
        await cards.propose(
            organization_id=team.org,
            user_id=team.member.id,
            arguments={
                "action": cards.DISCONNECT_APP,
                "scope": "mine",
                "connected_account_id": "ca_member",
            },
        )
        async with client_as(team.as_user(team.owner)) as c:
            mine = (await c.get("/api/v1/me/identity-cards")).json()["cards"]
        assert mine == []


@pytest.mark.asyncio
class TestConsent:
    async def test_consent_is_recorded_before_the_sign_in_with_what_was_shown(
        self, team, on, composio
    ):
        async with client_as(team.as_user(team.member)) as c:
            r = await c.post(
                "/api/v1/me/connections/start",
                json={
                    "toolkit": "gmail",
                    "scope": "mine",
                    "purpose": "Reply to Ravi's mail",
                    "return_to": "/overview?thread=abc",
                },
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["url"] == "https://connect.example/abc"
        assert any("password" in line for line in body["access"])
        view = await connections._consent(team.org, team.member.id, body["consent_id"])
        assert view.state == "authorizing" and view.purpose == "Reply to Ravi's mail"
        assert view.return_to == "/overview?thread=abc"
        assert view.access == body["access"]

    @pytest.mark.parametrize(
        "target", ["https://evil.example/", "//evil.example", "javascript:x"]
    )
    async def test_the_return_is_only_ever_inside_the_app(
        self, team, on, composio, target
    ):
        started = await connections.start(
            organization_id=team.org,
            user_id=team.member.id,
            toolkit="gmail",
            scope="mine",
            purpose=None,
            return_to=target,
            is_admin=False,
        )
        consent = await connections._consent(
            team.org, team.member.id, started["consent_id"]
        )
        assert consent.return_to is None

    async def test_ready_only_when_the_app_says_so(self, team, on, composio):
        started = await connections.start(
            organization_id=team.org,
            user_id=team.member.id,
            toolkit="gmail",
            scope="mine",
            purpose=None,
            return_to="/overview",
            is_admin=False,
        )
        still = await connections.complete(
            organization_id=team.org,
            user_id=team.member.id,
            consent_id=started["consent_id"],
        )
        assert still["state"] == "authorizing"
        composio.add(team.org, team.member.id, "gmail", "ca_new")
        done = await connections.complete(
            organization_id=team.org,
            user_id=team.member.id,
            consent_id=started["consent_id"],
        )
        assert done["state"] == "ready" and done["return_to"] == "/overview"
        assert done["connected_account_id"] == "ca_new"

    async def test_an_expired_sign_in_reads_expired(self, team, on, composio):
        composio.add(team.org, team.member.id, "gmail", "ca_old", status="EXPIRED")
        apps = await connections.apps(team.org, team.member.id, is_admin=False)
        (item,) = apps["items"]
        assert item["state"] == "expired" and "Reconnect" in item["reason"]

    async def test_wrong_workspace_is_refused(self, team, on, composio):
        started = await connections.start(
            organization_id=team.org,
            user_id=team.member.id,
            toolkit="gmail",
            scope="mine",
            purpose=None,
            return_to=None,
            is_admin=False,
        )
        # The member switched to their other workspace before returning.
        async with client_as(team.as_user(team.member, org=team.other_org)) as c:
            with patch.object(
                connections.features, "is_on", lambda name, org=None: True
            ):
                r = await c.post(
                    f"/api/v1/me/connections/consents/{started['consent_id']}/complete"
                )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "wrong_workspace"

    async def test_only_an_admin_connects_for_the_workspace(self, team, on, composio):
        async with client_as(team.as_user(team.member)) as c:
            r = await c.post(
                "/api/v1/me/connections/start",
                json={"toolkit": "gmail", "scope": "workspace"},
            )
        assert r.status_code == 403


@pytest.mark.asyncio
class TestHonestStates:
    async def test_not_configured_is_needs_setup_not_empty(self, team, on, monkeypatch):
        from api.services.integrations.composio import client

        monkeypatch.setattr(client, "is_configured", lambda: False)
        apps = await connections.apps(team.org, team.member.id, is_admin=False)
        assert apps["state"] == "needs_setup" and apps["reason"]

    async def test_a_failed_read_is_an_error_not_an_empty_list(
        self, team, on, composio
    ):
        composio.unavailable = True
        apps = await connections.apps(team.org, team.member.id, is_admin=False)
        assert apps["state"] == "error" and "incomplete" in apps["reason"]

    async def test_an_unfinished_sign_in_stays_visible(self, team, on, composio):
        await connections.start(
            organization_id=team.org,
            user_id=team.member.id,
            toolkit="notion",
            scope="mine",
            purpose=None,
            return_to=None,
            is_admin=False,
        )
        apps = await connections.apps(team.org, team.member.id, is_admin=False)
        assert [(i["toolkit"], i["state"]) for i in apps["items"]] == [
            ("notion", "authorizing")
        ]


@pytest.mark.asyncio
class TestDisconnectIsACard:
    async def _propose(self, team, account="ca_mine"):
        return await cards.propose(
            organization_id=team.org,
            user_id=team.member.id,
            arguments={
                "action": cards.DISCONNECT_APP,
                "scope": "mine",
                "connected_account_id": account,
            },
        )

    async def test_the_card_names_what_stops_and_is_private(
        self, team, on, composio, no_queue
    ):
        composio.add(team.org, team.member.id, "gmail", "ca_mine")
        told = await self._propose(team)
        event = await db_client.get_agent_event(
            told["event_id"], organization_id=team.org
        )
        payload = event.payload
        assert payload["label"] == "Disconnect Gmail (yours)"
        assert "reconnect" in payload["effect"]
        assert payload["private_to"] == team.member.id
        assert event.visibility == "private"
        # Never on the shared thread.
        shared = await db_client.agent_events(
            organization_id=team.org,
            assistant_thread=True,
            kinds=[AgentEventKind.ACTION_PROPOSED.value],
        )
        assert told["event_id"] not in {e.id for e in shared}
        # One ask, one card.
        again = await self._propose(team)
        assert again == {"status": "already_proposed", "event_id": told["event_id"]}

    async def test_nobody_else_can_approve_it(self, team, on, composio, no_queue):
        composio.add(team.org, team.member.id, "gmail", "ca_mine")
        told = await self._propose(team)
        event = await db_client.get_agent_event(
            told["event_id"], organization_id=team.org
        )
        with pytest.raises(actions.ActionError):
            await actions.settle(
                organization_id=team.org,
                event_id=told["event_id"],
                verb="confirm",
                user_id=team.owner.id,  # an admin, still not theirs
                version=event.payload["version"],
            )
        async with client_as(team.as_user(team.owner)) as c:
            r = await c.post(
                "/api/v1/timeline/actions/settle",
                json={
                    "event_id": told["event_id"],
                    "verb": "confirm",
                    "version": event.payload["version"],
                },
            )
        assert r.status_code == 409

    async def test_approved_it_revokes_at_the_app_and_future_use_stops(
        self, team, on, composio, no_queue
    ):
        composio.add(team.org, team.member.id, "gmail", "ca_mine")
        consent = await connections.start(
            organization_id=team.org,
            user_id=team.member.id,
            toolkit="gmail",
            scope="mine",
            purpose=None,
            return_to=None,
            is_admin=False,
        )
        await connections.complete(
            organization_id=team.org,
            user_id=team.member.id,
            consent_id=consent["consent_id"],
        )
        told = await self._propose(team)
        event = await db_client.get_agent_event(
            told["event_id"], organization_id=team.org
        )
        await actions.settle(
            organization_id=team.org,
            event_id=told["event_id"],
            verb="confirm",
            user_id=team.member.id,
            version=event.payload["version"],
        )
        await actions.run(told["event_id"], team.org)
        done = await db_client.get_agent_event(
            told["event_id"], organization_id=team.org
        )
        assert done.payload["state"] == "done"
        assert composio.deleted == ["ca_mine"]
        row = await connections._consent(
            team.org, team.member.id, consent["consent_id"]
        )
        assert row.state == "revoked" and row.revoked_by == team.member.id
        # Future use: the member's Gmail is no longer theirs to use.
        from api.services.integrations.composio import members

        assert (
            await members.resolve(
                organization_id=team.org, toolkit="gmail", user_id=team.member.id
            )
            is None
        )
        apps = await connections.apps(team.org, team.member.id, is_admin=False)
        assert [(i["toolkit"], i["state"]) for i in apps["items"]] == [
            ("gmail", "revoked")
        ]

    async def test_an_account_that_is_not_theirs_cannot_be_put_on_a_card(
        self, team, on, composio
    ):
        composio.add(team.org, team.owner.id, "gmail", "ca_owner")
        with pytest.raises(cards.CardError):
            await self._propose(team, account="ca_owner")

    async def test_the_app_refusing_leaves_it_connected_and_says_so(
        self, team, on, composio, no_queue
    ):
        from api.services.integrations.composio import client

        composio.add(team.org, team.member.id, "gmail", "ca_mine")
        composio.delete_raises = client.ComposioExecutionError("refused")
        told = await self._propose(team)
        event = await db_client.get_agent_event(
            told["event_id"], organization_id=team.org
        )
        await actions.settle(
            organization_id=team.org,
            event_id=told["event_id"],
            verb="confirm",
            user_id=team.member.id,
            version=event.payload["version"],
        )
        await actions.run(told["event_id"], team.org)
        failed = await db_client.get_agent_event(
            told["event_id"], organization_id=team.org
        )
        assert failed.payload["state"] == "failed"
        assert "Nothing changed" in failed.payload["error"]

    async def test_a_lost_answer_from_the_app_is_outcome_unknown(
        self, team, on, composio, no_queue
    ):
        from api.services.integrations.composio import client

        composio.add(team.org, team.member.id, "gmail", "ca_mine")
        composio.delete_raises = client.ComposioUnavailable("timeout")
        told = await self._propose(team)
        event = await db_client.get_agent_event(
            told["event_id"], organization_id=team.org
        )
        await actions.settle(
            organization_id=team.org,
            event_id=told["event_id"],
            verb="confirm",
            user_id=team.member.id,
            version=event.payload["version"],
        )
        await actions.run(told["event_id"], team.org)
        unknown = await db_client.get_agent_event(
            told["event_id"], organization_id=team.org
        )
        assert unknown.payload["state"] == "outcome_unknown"


@pytest.mark.asyncio
class TestTheModelCannotMintOne:
    async def test_propose_action_with_an_identity_kind_is_refused(
        self, team, on, composio
    ):
        composio.add(team.org, team.member.id, "gmail", "ca_mine")
        told = await actions.propose(
            organization_id=team.org,
            workflow_id=None,
            workflow_run_id=None,
            arguments={
                "action": cards.DISCONNECT_APP,
                "scope": "mine",
                "connected_account_id": "ca_mine",
                "owner_user_id": team.member.id,
            },
            in_channel=False,
        )
        assert told["status"] == "not_proposed"


@pytest.mark.asyncio
class TestChannels:
    @pytest.fixture(autouse=True)
    async def _fresh_checks(self, test_engine):
        from sqlalchemy import delete

        from api.db.identity_models import ChannelCheckModel

        async with db_client.async_session() as session:
            await session.execute(delete(ChannelCheckModel))
            await session.commit()

    @pytest.fixture
    def adapters(self):
        adapter = type("A", (), {"enabled": lambda self, org=None: True})()
        with patch(
            "api.services.messaging.channels.dispatch.adapter_for",
            lambda channel: adapter,
        ):
            yield

    async def test_switched_off_reads_turned_off_by_the_workspace(
        self, team, on, adapters
    ):
        rows = await connections.channels(team.org, team.member.id)
        assert {r["capability"] for r in rows} == {"disabled_by_policy"}

    async def test_configured_is_not_available_until_a_verified_message(
        self, team, on, adapters, monkeypatch
    ):
        monkeypatch.setattr(constants, "DECIBYL_CHANNELS_ENABLED", True)
        rows = {
            r["channel"]: r
            for r in await connections.channels(team.org, team.member.id)
        }
        assert rows["whatsapp"]["capability"] == "needs_setup"
        assert "not yet proven" in rows["whatsapp"]["reason"]
        await channel_health.saw_verified_inbound("whatsapp")
        rows = {
            r["channel"]: r
            for r in await connections.channels(team.org, team.member.id)
        }
        assert rows["whatsapp"]["capability"] == "available"
        # Telegram still needs its own switch and its own proof.
        assert rows["telegram"]["capability"] == "disabled_by_policy"
        # Messaging Decibyl there is not access to other messages.
        assert rows["whatsapp"]["reads_other_messages"] is False
        assert "24 hours" in rows["whatsapp"]["proactive"]

    async def test_delivery_failures_show_on_a_linked_channel(
        self, team, on, adapters, monkeypatch
    ):
        from api.services.messaging.channels import identities

        monkeypatch.setattr(constants, "DECIBYL_CHANNELS_ENABLED", True)
        await channel_health.saw_verified_inbound("whatsapp")
        code = await identities.start_link(
            organization_id=team.org, user_id=team.member.id, channel="whatsapp"
        )
        await identities.redeem(
            code=code,
            channel="whatsapp",
            external_id=f"+9198{team.member.id:08d}",
            display_name="Meera",
            conversation_ref={},
        )
        rows = {
            r["channel"]: r
            for r in await connections.channels(team.org, team.member.id)
        }
        assert rows["whatsapp"]["state"] == "ready"
        assert rows["whatsapp"]["linked"][0]["handle"] == f"{team.member.id:08d}"[-4:]
        await channel_health.delivery_failed("whatsapp", "refused")
        rows = {
            r["channel"]: r
            for r in await connections.channels(team.org, team.member.id)
        }
        assert (
            rows["whatsapp"]["state"] == "error"
            and rows["whatsapp"]["delivery_failing"]
        )

    async def test_inbound_after_the_signature_marks_it_verified(self, team, on):
        from api.services.messaging.channels import base, dispatch

        adapter = type(
            "A", (), {"enabled": lambda self, org=None: False, "send_text": AsyncMock()}
        )()
        with patch.object(dispatch, "adapter_for", lambda channel: adapter):
            await dispatch.handle(
                base.Inbound(
                    channel="telegram",
                    external_id="555",
                    message_id="m1",
                    text="hello",
                    ref={},
                )
            )
        checks = await connections.channel_checks()
        assert checks["telegram"].verified_inbound_at is not None

    async def test_nothing_is_recorded_while_off(self, team):
        await channel_health.saw_verified_inbound("slack")
        assert "slack" not in await connections.channel_checks()


@pytest.mark.asyncio
class TestPreview:
    async def test_the_access_shown_first_is_what_the_consent_stores(
        self, team, on, composio
    ):
        async with client_as(team.as_user(team.member)) as c:
            preview = (
                await c.get(
                    "/api/v1/me/connections/preview", params={"toolkit": "gmail"}
                )
            ).json()
            started = (
                await c.post("/api/v1/me/connections/start", json={"toolkit": "gmail"})
            ).json()
        assert preview["access"] == started["access"]
        assert (
            preview["can_connect_for_workspace"] is False
            and preview["per_person"] is True
        )

    async def test_a_made_up_name_is_refused(self, team, on, composio):
        async with client_as(team.as_user(team.member)) as c:
            r = await c.get(
                "/api/v1/me/connections/preview", params={"toolkit": "../etc"}
            )
        assert r.status_code == 404
