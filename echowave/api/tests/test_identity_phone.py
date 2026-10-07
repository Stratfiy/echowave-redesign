"""Phone and verification as one lifecycle (screen 24; launch stream identity).

Done when: every state reads from what exists (verification, numbers,
autopay) and Chat never waits on it; a failed source is "could not check",
never "none"; a number is labelled ready only after a test call and a
handover are recorded; requesting one is unavailable, and says why, until
who pays is decided; the amount is a marked placeholder; and a request is
an admin's action card that buys through the existing provisioning.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api import constants
from api.db import db_client
from api.db.models import (
    OrganizationKycModel,
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
)
from api.services.identity import phone
from api.services.workflow import actions
from api.tests.identity_support import client_as, flags, no_queue, team  # noqa: F401


@pytest.fixture
def on(monkeypatch):
    flags(monkeypatch, "IDENTITY_PHONE_ENABLED", "TASK_LEDGER_ENABLED")


async def _kyc(org: int, status: str, reason: str | None = None) -> None:
    async with db_client.async_session() as session:
        session.add(
            OrganizationKycModel(
                organization_id=org,
                status=status,
                carrier_rejection_reason=reason,
                carrier_reference="app-1" if status == "carrier_approved" else None,
            )
        )
        await session.commit()


async def _config(org: int) -> int:
    async with db_client.async_session() as session:
        config = TelephonyConfigurationModel(
            organization_id=org,
            name=f"cfg-{org}",
            provider="plivo",
            credentials={"auth_id": "MA", "auth_token": "t"},
            is_platform_managed=True,
        )
        session.add(config)
        await session.flush()
        config_id = config.id
        await session.commit()
        return config_id


async def _number(org: int, config: int, address: str, status: str = "active") -> int:
    async with db_client.async_session() as session:
        number = TelephonyPhoneNumberModel(
            organization_id=org,
            telephony_configuration_id=config,
            address=address,
            address_normalized=address,
            address_type="phone",
            country_code="IN",
            status=status,
            carrier_number_id="cn-1",
        )
        session.add(number)
        await session.flush()
        number_id = number.id
        await session.commit()
        return number_id


async def _helper(team) -> int:
    workflow = await db_client.create_workflow(
        name="Front desk",
        workflow_definition={},
        user_id=team.owner.id,
        organization_id=team.org,
    )
    return workflow.id


def _address(team, n=1) -> str:
    return f"+9180{team.org % 10_000_000:07d}{n}"


@pytest.mark.asyncio
class TestArrival:
    async def test_off_the_route_is_not_there(self, team):
        async with client_as(team.as_user(team.member)) as c:
            assert (await c.get("/api/v1/me/phone-identity")).status_code == 404

    async def test_not_requested_and_chat_does_not_wait(self, team, on):
        async with client_as(team.as_user(team.member)) as c:
            body = (await c.get("/api/v1/me/phone-identity")).json()
        assert (
            body["state"] == "not_requested"
            and "Chat works without one" in body["next_step"]
        )
        assert body["chat_needs_number"] is False
        # The payment flow is explained, the amount is a marked placeholder.
        assert body["payment"]["amount"] == phone.AMOUNT_PLACEHOLDER
        assert "PLACEHOLDER" in body["payment"]["amount"]
        assert len(body["payment"]["steps"]) >= 4
        assert body["request"]["available"] is False


@pytest.mark.asyncio
class TestLifecycle:
    @pytest.mark.parametrize(
        "status,state",
        [
            ("submitted", "pending_verification"),
            ("under_review", "pending_verification"),
            ("carrier_approved", "eligible"),
            ("suspended", "suspended"),
        ],
    )
    async def test_states_from_verification(self, team, on, status, state):
        await _kyc(team.org, status)
        assert (await phone.view(team.org))["state"] == state

    async def test_rejected_carries_the_next_step(self, team, on):
        await _kyc(team.org, "carrier_rejected", "The GST certificate is unreadable")
        view = await phone.view(team.org)
        assert view["state"] == "rejected"
        assert "GST certificate is unreadable" in view["next_step"]

    async def test_a_number_is_ready_only_after_the_test_call_and_handover(
        self, team, on
    ):
        await _kyc(team.org, "carrier_approved")
        number = await _number(team.org, await _config(team.org), _address(team))
        view = await phone.view(team.org)
        assert (
            view["state"] == "provisioning"
            and view["numbers"][0]["state"] == "provisioning"
        )
        async with client_as(team.as_user(team.member)) as c:
            r = await c.post(
                f"/api/v1/me/phone-identity/numbers/{number}/readiness",
                json={"incoming_call_ok": True, "escalation_ok": True},
            )
            assert r.status_code == 403  # an admin's to record
        async with client_as(team.as_user(team.owner)) as c:
            half = await c.post(
                f"/api/v1/me/phone-identity/numbers/{number}/readiness",
                json={"incoming_call_ok": True, "escalation_ok": False},
            )
            assert half.json()["state"] == "provisioning"
            full = await c.post(
                f"/api/v1/me/phone-identity/numbers/{number}/readiness",
                json={"incoming_call_ok": True, "escalation_ok": True},
            )
        assert full.json()["state"] == "active"

    async def test_another_workspaces_number_cannot_be_marked(self, team, on):
        number = await _number(
            team.other_org, await _config(team.other_org), _address(team, 2)
        )
        async with client_as(team.as_user(team.owner)) as c:
            r = await c.post(
                f"/api/v1/me/phone-identity/numbers/{number}/readiness",
                json={"incoming_call_ok": True, "escalation_ok": True},
            )
        assert r.status_code == 404

    async def test_released_numbers_read_released(self, team, on):
        await _kyc(team.org, "carrier_approved")
        await _number(
            team.org, await _config(team.org), _address(team, 3), status="released"
        )
        assert (await phone.view(team.org))["state"] == "released"


@pytest.mark.asyncio
class TestPartialFailure:
    async def test_a_failed_helper_list_is_not_no_helpers_and_blocks_assignment(
        self, team, on, monkeypatch
    ):
        await _kyc(team.org, "carrier_approved")
        monkeypatch.setattr(constants, "NUMBER_PAYMENT_POLICY", "sponsored")
        with patch.object(
            db_client,
            "get_all_workflows_for_listing",
            AsyncMock(side_effect=RuntimeError("db")),
        ):
            view = await phone.view(team.org)
        assert view["sources"]["helpers"] == "failed" and view["helpers"] is None
        assert view["request"]["available"] is False
        assert "could not load your helpers" in view["request"]["reason"]

    async def test_a_failed_number_list_is_not_an_empty_one(self, team, on):
        with patch.object(
            phone, "_numbers", AsyncMock(side_effect=RuntimeError("down"))
        ):
            view = await phone.view(team.org)
        assert view["sources"]["numbers"] == "failed" and view["numbers"] is None
        ok = await phone.view(team.org)
        assert ok["numbers"] == []


@pytest.mark.asyncio
class TestRequestingANumber:
    async def test_undecided_payment_means_unavailable_with_the_reason(
        self, team, on, no_queue
    ):
        await _kyc(team.org, "carrier_approved")
        helper = await _helper(team)
        config = await _config(team.org)
        view = await phone.view(team.org)
        assert view["request"]["available"] is False
        assert "who pays" in view["request"]["reason"]
        async with client_as(team.as_user(team.owner)) as c:
            r = await c.post(
                "/api/v1/me/phone-identity/request",
                json={
                    "telephony_configuration_id": config,
                    "address": _address(team, 4),
                    "helper_id": helper,
                },
            )
        assert r.status_code == 409 and "who pays" in r.json()["detail"]

    async def test_only_an_admin_requests_and_it_is_a_card_that_buys_once(
        self, team, on, no_queue, monkeypatch
    ):
        monkeypatch.setattr(constants, "NUMBER_PAYMENT_POLICY", "sponsored")
        await _kyc(team.org, "carrier_approved")
        helper = await _helper(team)
        config = await _config(team.org)
        body = {
            "telephony_configuration_id": config,
            "address": _address(team, 5),
            "helper_id": helper,
        }
        async with client_as(team.as_user(team.member)) as c:
            assert (
                await c.post("/api/v1/me/phone-identity/request", json=body)
            ).status_code == 403
        async with client_as(team.as_user(team.owner)) as c:
            r = await c.post("/api/v1/me/phone-identity/request", json=body)
        assert r.status_code == 200, r.text
        card = r.json()
        assert card["label"] == f"Get {_address(team, 5)} for Front desk"
        assert phone.AMOUNT_PLACEHOLDER in card["effect"]
        bought = AsyncMock(
            return_value=SimpleNamespace(phone_number_id=1, address=_address(team, 5))
        )
        with patch("api.services.telephony.provisioning.provision", bought):
            await actions.settle(
                organization_id=team.org,
                event_id=card["event_id"],
                verb="confirm",
                user_id=team.owner.id,
                version=card["version"],
            )
            await actions.run(card["event_id"], team.org)
            await actions.run(card["event_id"], team.org)
        assert bought.await_count == 1
        assert bought.await_args.kwargs["inbound_workflow_id"] == helper
        done = await db_client.get_agent_event(
            card["event_id"], organization_id=team.org
        )
        assert (
            done.payload["state"] == "done"
            and "test call" in done.payload["done"]["note"]
        )

    async def test_a_helper_from_another_workspace_cannot_be_assigned(
        self, team, on, no_queue, monkeypatch
    ):
        monkeypatch.setattr(constants, "NUMBER_PAYMENT_POLICY", "sponsored")
        await _kyc(team.org, "carrier_approved")
        foreign = await db_client.create_workflow(
            name="Theirs",
            workflow_definition={},
            user_id=team.stranger.id,
            organization_id=team.other_org,
        )
        async with client_as(team.as_user(team.owner)) as c:
            r = await c.post(
                "/api/v1/me/phone-identity/request",
                json={
                    "telephony_configuration_id": await _config(team.org),
                    "address": _address(team, 6),
                    "helper_id": foreign.id,
                },
            )
        assert r.status_code == 409 and "helper is not here" in r.json()["detail"]

    async def test_autopay_policy_needs_an_authorised_mandate(
        self, team, on, monkeypatch
    ):
        monkeypatch.setattr(constants, "NUMBER_PAYMENT_POLICY", "provider_autopay")
        await _kyc(team.org, "carrier_approved")
        helper = await _helper(team)
        from api.services.identity.cards import CardError

        with pytest.raises(CardError, match="autopay"):
            await phone.check_request(
                team.org,
                telephony_configuration_id=await _config(team.org),
                address=_address(team, 7),
                helper_id=helper,
            )
