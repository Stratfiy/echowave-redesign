"""Stream `reach`: ordering from a list in Chat, always through a card.

Done when: a list becomes an order card showing the items, every charge,
the total, the address and how it is paid; nothing is placed until the
person who asked approves that exact card; a colleague can neither see the
details nor approve it; a bill that changed is refused, not placed; a
placement whose outcome is lost is "outcome unknown" and is never placed
twice; and an app without access says "needs setup".
"""

from __future__ import annotations

import pytest

from api import constants
from api.db import db_client
from api.services.reach import wire
from api.services.reach.ordering import service as ordering
from api.services.workflow import actions, decibyl
from api.tests.support import reach_fakes
from api.tests.support.reach_fixtures import (  # noqa: F401
    acting_as,
    call,
    client_as,
    connect_zomato,
    fake_servers,
    no_queue,
    people,
    reach_on,
)

pytestmark = pytest.mark.asyncio

LIST = [{"item_id": "i1", "quantity": 2}, {"item_id": "i3", "quantity": 1}]


async def _card(org, user_id, **extra):
    with acting_as(user_id):
        result = await decibyl._tool(
            org,
            call(
                "order_prepare",
                app="zomato",
                store_id="r1",
                items=LIST,
                address_id="a1",
                **extra,
            ),
            user_id,
        )
    assert result["status"] == "proposed", result
    event = await db_client.get_agent_event(result["event_id"], organization_id=org)
    return result, event


async def _confirm_and_run(org, event, user_id):
    payload = await actions.settle(
        organization_id=org,
        event_id=event.id,
        verb="confirm",
        user_id=user_id,
        version=(event.payload or {}).get("version"),
    )
    await actions.run(event.id, org)
    return (
        await db_client.get_agent_event(event.id, organization_id=org)
    ).payload, payload


class TestTheCard:
    async def test_no_address_asks_rather_than_picks(self, reach_on, people):
        await connect_zomato(people.org, people.a.id)
        with acting_as(people.a.id):
            result = await decibyl._tool(
                people.org,
                call("order_prepare", app="zomato", store_id="r1", items=LIST),
                people.a.id,
            )
        assert result["status"] == "not_proposed"
        assert [a["id"] for a in result["addresses"]] == ["a1", "a2"]
        assert "Never pick one" in result["reason"]

    async def test_the_card_shows_items_charges_total_address_and_payment(
        self, reach_on, people
    ):
        await connect_zomato(people.org, people.a.id)
        _, event = await _card(people.org, people.a.id)
        payload = event.payload
        assert payload["action"] == "place_order" and payload["state"] == "proposed"
        assert payload["owner_user_id"] == people.a.id
        # 2 x 220 + 60 = 500; delivery 30; taxes 25 -> 555.
        assert payload["label"] == "Order from Spice Route on Zomato: 3 items, ₹555"
        assert "₹555" in payload["effect"] and "Home" in payload["effect"]
        assert "UPI" in payload["effect"]
        async with client_as(people.a) as client:
            detail = (
                await client.get(f"/api/v1/reach/orders/{payload['args']['draft']}")
            ).json()
        assert [
            (i["name"], i["quantity"], i["line_total_paise"]) for i in detail["items"]
        ] == [
            ("Paneer Tikka", 2, 44000),
            ("Coke 500ml", 1, 6000),
        ]
        assert [(c["label"], c["amount_paise"]) for c in detail["charges"]] == [
            ("Delivery", 3000),
            ("Taxes", 2500),
        ]
        assert detail["total_paise"] == 55500
        assert detail["address"]["line"].startswith("12 MG Road")
        assert detail["payment"]["label"] == "UPI (scan the QR Zomato shows)"
        assert detail["card_event_id"] == event.id
        # Nothing was placed by proposing.
        assert reach_fakes.ORDERS == {}

    async def test_the_address_is_not_on_the_shared_card(self, reach_on, people):
        await connect_zomato(people.org, people.a.id)
        _, event = await _card(people.org, people.a.id)
        assert "MG Road" not in str(event.payload)

    async def test_a_colleague_cannot_see_the_details(self, reach_on, people):
        await connect_zomato(people.org, people.a.id)
        _, event = await _card(people.org, people.a.id)
        async with client_as(people.b) as client:
            response = await client.get(
                f"/api/v1/reach/orders/{event.payload['args']['draft']}"
            )
        assert response.status_code == 404

    async def test_the_coupon_is_applied_on_the_card(self, reach_on, people):
        await connect_zomato(people.org, people.a.id)
        _, event = await _card(people.org, people.a.id, coupon="SAVE50")
        assert event.payload["label"].endswith("₹505")


class TestApproval:
    async def test_only_the_owner_can_approve_decline_or_undo(
        self, reach_on, people, no_queue
    ):
        await connect_zomato(people.org, people.a.id)
        _, event = await _card(people.org, people.a.id)
        for verb in ("confirm", "decline", "undo"):
            with pytest.raises(actions.ActionError, match="Only the person"):
                await actions.settle(
                    organization_id=people.org,
                    event_id=event.id,
                    verb=verb,
                    user_id=people.b.id,
                    version=event.payload.get("version"),
                )

    async def test_approved_it_is_placed_once(self, reach_on, people, no_queue):
        await connect_zomato(people.org, people.a.id)
        _, event = await _card(people.org, people.a.id)
        after, _ = await _confirm_and_run(people.org, event, people.a.id)
        assert after["state"] == "done"
        assert after["done"]["note"].startswith("Ordered on Zomato: order Z1001, ₹555.")
        assert after["result"]["payment_link"].startswith("https://pay.example.com/")
        assert len(reach_fakes.ORDERS) == 1
        # A second job for the same card does nothing.
        await actions.run(event.id, people.org)
        assert len(reach_fakes.ORDERS) == 1
        draft = await ordering.get_draft(
            people.org, people.a.id, after["args"]["draft"]
        )
        assert draft.status == "placed" and draft.provider_order_id == "Z1001"

    async def test_declined_it_can_never_be_placed(self, reach_on, people, no_queue):
        await connect_zomato(people.org, people.a.id)
        _, event = await _card(people.org, people.a.id)
        await actions.settle(
            organization_id=people.org,
            event_id=event.id,
            verb="decline",
            user_id=people.a.id,
        )
        draft = await ordering.get_draft(
            people.org, people.a.id, event.payload["args"]["draft"]
        )
        assert draft.status == "cancelled"
        with pytest.raises(ordering.OrderError):
            await ordering.place(
                organization_id=people.org,
                args=event.payload["args"],
                confirmer=people.a.id,
                idempotency_key="x",
            )
        assert reach_fakes.ORDERS == {}

    async def test_undone_in_the_window_nothing_is_placed(
        self, reach_on, people, no_queue
    ):
        await connect_zomato(people.org, people.a.id)
        _, event = await _card(people.org, people.a.id)
        await actions.settle(
            organization_id=people.org,
            event_id=event.id,
            verb="confirm",
            user_id=people.a.id,
            version=event.payload.get("version"),
        )
        await actions.settle(
            organization_id=people.org,
            event_id=event.id,
            verb="undo",
            user_id=people.a.id,
        )
        await actions.run(event.id, people.org)
        assert reach_fakes.ORDERS == {}

    async def test_switched_off_after_approval_nothing_is_placed(
        self, reach_on, people, no_queue, monkeypatch
    ):
        await connect_zomato(people.org, people.a.id)
        _, event = await _card(people.org, people.a.id)
        await actions.settle(
            organization_id=people.org,
            event_id=event.id,
            verb="confirm",
            user_id=people.a.id,
            version=event.payload.get("version"),
        )
        monkeypatch.setattr(constants, "ORDERING_ENABLED", False)
        await actions.run(event.id, people.org)
        after = (
            await db_client.get_agent_event(event.id, organization_id=people.org)
        ).payload
        assert after["state"] == "failed" and "switched off" in after["error"]
        assert reach_fakes.ORDERS == {}

    async def test_a_changed_bill_is_refused_not_placed(
        self, reach_on, people, no_queue
    ):
        await connect_zomato(people.org, people.a.id)
        _, event = await _card(people.org, people.a.id)
        reach_fakes.FAULTS["price_bump"] = 10
        after, _ = await _confirm_and_run(people.org, event, people.a.id)
        assert after["state"] == "failed"
        assert "The bill changed since you approved it (it was ₹555" in after["error"]
        assert reach_fakes.ORDERS == {}

    async def test_the_app_refusing_is_a_failed_card(self, reach_on, people, no_queue):
        await connect_zomato(people.org, people.a.id)
        _, event = await _card(people.org, people.a.id)
        reach_fakes.FAULTS["checkout"] = "refuse"
        after, _ = await _confirm_and_run(people.org, event, people.a.id)
        assert after["state"] == "failed" and "closed" in after["error"]

    async def test_a_lost_answer_is_outcome_unknown_then_reconciled(
        self, reach_on, people, no_queue, monkeypatch
    ):
        await connect_zomato(people.org, people.a.id)
        _, event = await _card(people.org, people.a.id)
        monkeypatch.setattr(wire, "TIMEOUT_SECS", 1.5)
        reach_fakes.FAULTS.update(checkout="hang", hang_secs=4)
        after, _ = await _confirm_and_run(people.org, event, people.a.id)
        assert after["state"] == "outcome_unknown"
        assert "Please do not order it again" in after["error"]
        # Never fired again blind.
        await actions.run(event.id, people.org)
        assert len(reach_fakes.ORDERS) == 1
        reach_fakes.FAULTS.update(checkout="ok")
        async with client_as(people.a) as client:
            checked = await client.post(
                f"/api/v1/reach/orders/{after['args']['draft']}/check"
            )
        assert checked.status_code == 200 and checked.json()["status"] == "placed"
        settled = (
            await db_client.get_agent_event(event.id, organization_id=people.org)
        ).payload
        assert (
            settled["state"] == "done" and "It went through" in settled["done"]["note"]
        )

    async def test_a_card_cannot_be_put_in_a_colleagues_name(self, reach_on, people):
        await connect_zomato(people.org, people.a.id)
        _, event = await _card(people.org, people.a.id)
        # B's turn names A's draft: refused before any card is written.
        with acting_as(people.b.id):
            result = await actions.propose(
                organization_id=people.org,
                workflow_id=None,
                workflow_run_id=None,
                arguments={
                    "action": "place_order",
                    "draft": event.payload["args"]["draft"],
                },
                in_channel=False,
            )
        assert result["status"] == "not_proposed"


class TestEditing:
    async def test_an_edit_is_a_new_card_and_the_old_one_is_declined(
        self, reach_on, people, no_queue
    ):
        await connect_zomato(people.org, people.a.id)
        _, event = await _card(people.org, people.a.id)
        draft = event.payload["args"]["draft"]
        async with client_as(people.a) as client:
            response = await client.post(
                f"/api/v1/reach/orders/{draft}/revise",
                json={"items": [{"item_id": "i1", "quantity": 1}], "address_id": "a2"},
            )
        assert response.status_code == 200
        new_event = await db_client.get_agent_event(
            response.json()["event_id"], organization_id=people.org
        )
        # 220 + 60, delivery 30, taxes 14.
        assert new_event.payload["label"].endswith("2 items, ₹324")
        old = await db_client.get_agent_event(event.id, organization_id=people.org)
        assert old.payload["state"] == "declined"
        assert new_event.payload["args"]["digest"] != event.payload["args"]["digest"]
        # The old approval cannot be used on the new figures.
        with pytest.raises(actions.ActionError):
            await actions.settle(
                organization_id=people.org,
                event_id=event.id,
                verb="confirm",
                user_id=people.a.id,
                version=event.payload.get("version"),
            )


class TestHonestStates:
    async def test_not_connected_puts_a_chip_on_the_thread(self, reach_on, people):
        with acting_as(people.a.id):
            result = await decibyl._tool(
                people.org,
                call("order_search", app="zomato", query="paneer"),
                people.a.id,
            )
        assert result["status"] == "offered" and "NOT connected" in result["note"]
        rows = await db_client.agent_events(
            organization_id=people.org,
            kinds=["reach_connect_offered"],
            limit=5,
            assistant_thread=True,
            viewer_id=people.a.id,
        )
        chip = rows[0].payload
        assert chip["provider"] == "zomato" and chip["state"] == "available"
        assert chip["offered_to"] == people.a.id

    async def test_an_app_without_access_says_needs_setup(self, reach_on, people):
        with acting_as(people.a.id):
            result = await decibyl._tool(
                people.org,
                call("order_search", app="swiggy", query="milk"),
                people.a.id,
            )
        assert result["status"] == "needs_setup"
        assert "Builders Club" in result["note"]

    async def test_no_server_address_is_needs_setup_for_zomato_too(
        self, reach_on, monkeypatch, people
    ):
        monkeypatch.setattr(constants, "ZOMATO_MCP_URL", None)
        async with client_as(people.a) as client:
            states = {
                p["provider"]: p["state"]
                for p in (await client.get("/api/v1/reach/providers")).json()[
                    "providers"
                ]
            }
        assert states["zomato"] == "needs_setup"

    async def test_search_finds_items_as_data(self, reach_on, people):
        await connect_zomato(people.org, people.a.id)
        with acting_as(people.a.id):
            result = await decibyl._tool(
                people.org,
                call("order_search", app="zomato", query="paneer"),
                people.a.id,
            )
        assert result["untrusted"] is True and "Paneer Tikka" in result["data"]
        assert decibyl._was_a_read(call("order_search"), result)
