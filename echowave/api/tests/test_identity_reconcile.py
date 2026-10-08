"""Reconciling sends whose outcome is unknown, per provider (launch stream
identity; CONTROLS.md section 3 left this to the streams that own sends).

Done when: each provider's own evidence settles the card -- done with that
evidence, or failed with its reason -- only ever from outcome unknown;
nothing is ever sent again; a provider that cannot be asked hands the
question to the person who approved it, whose answer settles it as theirs;
nobody else can answer; and while the switch is off nothing changes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from api.db import db_client
from api.db.identity_models import EmailIdentityModel, EmailIdentitySendModel
from api.enums import AgentEventKind, AgentEventVisibility
from api.services.identity import reconcile
from api.services.workflow import actions, agent_timeline
from api.tests.identity_support import client_as, composio, flags, team  # noqa: F401


@pytest.fixture
def on(monkeypatch):
    flags(monkeypatch, "IDENTITY_RECONCILIATION_ENABLED", "TASK_LEDGER_ENABLED")


async def _unknown(
    team, action: str, args: dict, *, private: bool = False, minutes_ago: int = 15
) -> int:
    fired = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    payload = {
        "action": action,
        "args": args,
        "label": f"Test {action}",
        "state": actions.OUTCOME_UNKNOWN,
        "confirmed": {"by": team.member.id, "at": fired.isoformat(), "version": "v1"},
        "version": "v1",
        "idempotency_key": f"card:x:{action}:{team.member.id}:{minutes_ago}",
        "fires_at": fired.isoformat(),
        "error": "We are checking whether this was delivered. Please do not send it again.",
    }
    if private:
        payload["private_to"] = team.member.id
    event_id = await agent_timeline.record(
        organization_id=team.org,
        kind=AgentEventKind.ACTION_PROPOSED.value,
        summary=payload["label"],
        payload=payload,
        in_channel=False,
        visibility=AgentEventVisibility.PRIVATE.value if private else None,
    )
    assert event_id
    return event_id


async def _state(team, event_id):
    row = await db_client.get_agent_event(event_id, organization_id=team.org)
    return row.payload


async def _identity_send(team, event_id: int, state: str) -> None:
    async with db_client.async_session() as session:
        identity = EmailIdentityModel(
            user_id=team.member.id,
            organization_id=team.org,
            alias=f"r{event_id}x",
            state="active",
        )
        session.add(identity)
        await session.flush()
        session.add(
            EmailIdentitySendModel(
                identity_id=identity.id,
                user_id=team.member.id,
                card_event_id=event_id,
                message_id=f"<{event_id}@decibyl.test>",
                to_address="ravi@example.com",
                state=state,
            )
        )
        await session.commit()


@pytest.fixture
def never_sends():
    with patch.object(actions, "_execute", AsyncMock()) as execute:
        yield execute
    assert execute.await_count == 0, "a reconciled card must never be sent again"


@pytest.mark.asyncio
class TestSwitchedOff:
    async def test_nothing_changes(self, team, never_sends):
        event_id = await _unknown(team, "send_identity_email", {}, private=True)
        assert await reconcile.sweep() == {
            "delivered": 0,
            "not_delivered": 0,
            "asked": 0,
            "waiting": 0,
        }
        assert (await _state(team, event_id))["state"] == "outcome_unknown"


@pytest.mark.asyncio
class TestPerProvider:
    async def test_our_mail_server_accepted_it(self, team, on, never_sends):
        event_id = await _unknown(team, "send_identity_email", {}, private=True)
        await _identity_send(team, event_id, "accepted")
        with patch("api.services.identity.notifications.notify", AsyncMock()) as notify:
            await reconcile.sweep()
        payload = await _state(team, event_id)
        assert payload["state"] == "done"
        assert payload["reconciled"]["provider"] == "identity_email"
        assert "accepted" in payload["reconciled"]["evidence"]
        # The person who approved it is told.
        assert notify.await_args.args[0] == team.member.id
        assert notify.await_args.kwargs["topic"] == "task_updates"

    async def test_it_never_reached_the_mail_server(self, team, on, never_sends):
        event_id = await _unknown(team, "send_identity_email", {}, private=True)
        await reconcile.sweep()
        payload = await _state(team, event_id)
        assert payload["state"] == "failed" and payload["reason_code"] == "never_sent"

    async def test_whatsapp_status_matched_on_the_cards_key(
        self, team, on, never_sends
    ):
        event_id = await _unknown(
            team, "send_document", {"channel": "whatsapp", "to": "+91"}
        )
        key = (await _state(team, event_id))["idempotency_key"]
        stored = await reconcile.record_whatsapp_statuses(
            {
                "entry": [
                    {
                        "changes": [
                            {
                                "value": {
                                    "statuses": [
                                        {
                                            "id": f"wamid.{event_id}",
                                            "status": "delivered",
                                            "biz_opaque_callback_data": f"decibyl:{key}",
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ]
            }
        )
        assert stored == 1
        await reconcile.sweep()
        payload = await _state(team, event_id)
        assert (
            payload["state"] == "done"
            and "WhatsApp reported it delivered" in payload["done"]["note"]
        )

    async def test_a_disconnect_still_listed_at_the_app_failed(
        self, team, on, composio, never_sends, monkeypatch
    ):
        monkeypatch.setattr("api.constants.CONNECTIONS_PER_PERSON_ENABLED", True)
        composio.add(team.org, team.member.id, "gmail", "ca_still")
        kept = await _unknown(
            team,
            "disconnect_app",
            {"scope": "mine", "connected_account_id": "ca_still"},
            private=True,
        )
        gone = await _unknown(
            team,
            "disconnect_app",
            {"scope": "mine", "connected_account_id": "ca_gone"},
            private=True,
            minutes_ago=16,
        )
        await reconcile.sweep()
        assert (await _state(team, kept))["state"] == "failed"
        assert (await _state(team, gone))["state"] == "done"

    async def test_a_disconnect_found_done_is_recorded_as_the_run_would(
        self, team, on, composio, never_sends, monkeypatch
    ):
        """Phase 3: the card settled done, but the consent stayed ready, the
        member's registry row stayed (the staff screen reads it) and nothing
        said connection_revoked -- the run had raised before recording it."""
        from sqlalchemy import select

        from api.db.identity_models import ConnectionConsentModel
        from api.db.models import MemberConnectionModel
        from api.services.identity import connections

        monkeypatch.setattr("api.constants.CONNECTIONS_PER_PERSON_ENABLED", True)
        monkeypatch.setattr("api.constants.IDENTITY_CONNECTIONS_ENABLED", True)
        composio.add(team.org, team.member.id, "gmail", "ca_lost")
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
        # It went at the app; the answer never came back.
        composio.accounts[f"decibyl_org_{team.org}_user_{team.member.id}"] = []
        event_id = await _unknown(
            team,
            "disconnect_app",
            {"scope": "mine", "toolkit": "gmail", "connected_account_id": "ca_lost"},
            private=True,
        )
        with patch("api.services.events.emit", AsyncMock()) as emit:
            await reconcile.sweep()
        assert (await _state(team, event_id))["state"] == "done"
        async with db_client.async_session() as session:
            row = await session.get(ConnectionConsentModel, consent["consent_id"])
            registry = list(
                await session.scalars(
                    select(MemberConnectionModel).where(
                        MemberConnectionModel.organization_id == team.org,
                        MemberConnectionModel.user_id == team.member.id,
                        MemberConnectionModel.toolkit == "gmail",
                    )
                )
            )
        assert row.state == "revoked" and row.revoked_by == team.member.id
        assert registry == []
        assert [c.args[0] for c in emit.await_args_list].count(
            "connection_revoked"
        ) == 1

    async def test_a_provider_that_cannot_answer_waits_then_asks_the_person(
        self, team, on, never_sends
    ):
        fresh = await _unknown(team, "run_tool", {"toolkit": "gmail"}, minutes_ago=5)
        old = await _unknown(team, "run_tool", {"toolkit": "gmail"}, minutes_ago=45)
        with patch("api.services.identity.notifications.notify", AsyncMock()) as notify:
            counts = await reconcile.sweep()
        assert counts["asked"] >= 1
        assert "needs_person" not in (
            (await _state(team, fresh)).get("reconcile") or {}
        )
        asked = await _state(team, old)
        assert (
            asked["state"] == "outcome_unknown"
            and asked["reconcile"]["needs_person"] is True
        )
        assert notify.await_args.kwargs["title"] == "Did it arrive?"
        # Asked once, not every sweep.
        with patch("api.services.identity.notifications.notify", AsyncMock()) as again:
            await reconcile.sweep()
        assert again.await_count == 0


@pytest.mark.asyncio
class TestThePersonAnswers:
    async def test_the_approver_settles_it_as_theirs(self, team, on, never_sends):
        event_id = await _unknown(
            team, "run_tool", {"toolkit": "gmail"}, minutes_ago=45
        )
        async with client_as(team.as_user(team.member)) as c:
            r = await c.post(f"/api/v1/me/outcomes/{event_id}", json={"arrived": False})
        assert r.status_code == 200 and r.json()["state"] == "failed"
        payload = await _state(team, event_id)
        assert payload["reconciled"]["by"] == f"person:{team.member.id}"
        async with client_as(team.as_user(team.member)) as c:
            again = await c.post(
                f"/api/v1/me/outcomes/{event_id}", json={"arrived": True}
            )
        assert again.status_code == 409

    async def test_nobody_else_answers_a_private_card(self, team, on, never_sends):
        event_id = await _unknown(team, "send_identity_email", {}, private=True)
        async with client_as(team.as_user(team.owner)) as c:  # an admin
            r = await c.post(f"/api/v1/me/outcomes/{event_id}", json={"arrived": True})
        assert r.status_code == 404
        async with client_as(team.as_user(team.stranger, org=team.other_org)) as c:
            r = await c.post(f"/api/v1/me/outcomes/{event_id}", json={"arrived": True})
        assert r.status_code == 404
        assert (await _state(team, event_id))["state"] == "outcome_unknown"

    async def test_off_the_route_is_not_there(self, team, never_sends):
        event_id = await _unknown(team, "run_tool", {"toolkit": "gmail"})
        async with client_as(team.as_user(team.member)) as c:
            r = await c.post(f"/api/v1/me/outcomes/{event_id}", json={"arrived": True})
        assert r.status_code == 404


@pytest.mark.asyncio
class TestTheKeyTravelsWithTheMessage:
    async def _send(self):
        from api.services.messaging import send

        captured = {}

        class Response:
            status_code = 200

            def json(self):
                return {"messages": [{"id": "wamid.1"}]}

        class Client:
            async def post(self, url, headers=None, json=None):
                captured.update(json)
                return Response()

        await send._send_meta_whatsapp(
            Client(),
            {"access_token": "t", "phone_number_id": "p"},
            to="+919876543210",
            body="hi",
        )
        return captured

    async def test_with_a_key_meta_is_given_it(self):
        from api.services.messaging.send import callback_data

        with callback_data("decibyl:card:1:v1"):
            sent = await self._send()
        assert sent["biz_opaque_callback_data"] == "decibyl:card:1:v1"

    async def test_without_one_nothing_is_added(self):
        assert "biz_opaque_callback_data" not in await self._send()

    async def test_the_key_is_only_minted_while_on(self, team, monkeypatch):
        payload = {"idempotency_key": "card:1:v1"}
        assert reconcile.callback_key(team.org, payload) is None
        monkeypatch.setattr("api.constants.IDENTITY_RECONCILIATION_ENABLED", True)
        assert reconcile.callback_key(team.org, payload) == "decibyl:card:1:v1"
