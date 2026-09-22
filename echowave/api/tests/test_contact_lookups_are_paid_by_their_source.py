"""OP-3: who pays for a contact lookup.

Own app: the tool-call fee, at the connector's rate. Platform key: the
fee plus the vendor's price per verified address. Neither: nothing looked
up, a connect line told, no key asked for.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.services.billing import events, lookup_source

MOD = "api.services.billing.lookup_source"


def _tool(toolkit: str):
    return SimpleNamespace(definition={"config": {"toolkit": toolkit}})


class TestTheSource:
    async def test_own_app_first(self):
        with (
            patch(
                "api.services.workflow.connected_tools.list_for_organization",
                AsyncMock(return_value=[_tool("gmail"), _tool("Apollo")]),
            ),
            patch(f"{MOD}._platform_provider", AsyncMock(return_value="hunter")),
        ):
            src = await lookup_source.source_for(1)
        assert src == lookup_source.LookupSource("own", "apollo")
        assert src.as_dict()["charged_as"] == "tool call on your own account"

    async def test_platform_key_second(self):
        with (
            patch(
                "api.services.workflow.connected_tools.list_for_organization",
                AsyncMock(return_value=[_tool("gmail")]),
            ),
            patch(f"{MOD}._platform_provider", AsyncMock(return_value="hunter")),
        ):
            src = await lookup_source.source_for(1)
        assert src == lookup_source.LookupSource("platform", "hunter")

    async def test_neither_is_a_connect_line_not_a_key_request(self):
        with (
            patch(
                "api.services.workflow.connected_tools.list_for_organization",
                AsyncMock(return_value=[]),
            ),
            patch(f"{MOD}._platform_provider", AsyncMock(return_value=None)),
        ):
            src = await lookup_source.source_for(1)
        assert not src.usable
        told = src.as_dict()["reason"]
        assert "Connect a contact-data app" in told
        assert "api key" not in told.lower()

    async def test_a_book_that_cannot_be_read_is_no_source(self):
        with (
            patch(
                "api.services.workflow.connected_tools.list_for_organization",
                AsyncMock(side_effect=RuntimeError("db away")),
            ),
            patch(f"{MOD}._platform_provider", AsyncMock(side_effect=RuntimeError())),
        ):
            assert (await lookup_source.source_for(1)).kind == "none"

    def test_the_platform_provider_must_be_a_contact_data_one(self):
        assert lookup_source.own_toolkit([_tool("serper")]) is None
        assert lookup_source.own_toolkit([_tool("hunter_io")]) == "hunter_io"


class TestTheCharge:
    async def test_own_app_pays_the_fee_alone(self):
        with (
            patch(
                "api.services.billing.events.charge_in_own_session", AsyncMock()
            ) as fee,
            patch(
                "api.services.billing.data_costs.debit_lookup_in_own_session",
                AsyncMock(),
            ) as passthrough,
        ):
            out = await lookup_source.charge(
                organization_id=1,
                source=lookup_source.LookupSource("own", "apollo"),
                verified=5,
                ref_id="r",
                workflow_id=42,
            )
        assert fee.await_args.kwargs["event"] == events.TOOL_CALL
        assert fee.await_args.kwargs["workflow_id"] == 42
        passthrough.assert_not_awaited()
        assert out["charged"] is True
        assert "verified" not in out

    async def test_a_premium_own_app_pays_the_premium_fee(self, monkeypatch):
        monkeypatch.setattr(events, "PREMIUM_CONNECTORS", frozenset({"zoominfo"}))
        with patch(
            "api.services.billing.events.charge_in_own_session", AsyncMock()
        ) as fee:
            await lookup_source.charge(
                organization_id=1,
                source=lookup_source.LookupSource("own", "zoominfo"),
                verified=1,
                ref_id="r",
            )
        assert fee.await_args.kwargs["event"] == events.TOOL_CALL_PREMIUM

    async def test_platform_pays_the_fee_and_each_verified_address(self):
        with (
            patch("api.services.billing.events.charge_in_own_session", AsyncMock()),
            patch(
                "api.services.billing.data_costs.debit_lookup_in_own_session",
                AsyncMock(return_value=480),
            ) as passthrough,
        ):
            out = await lookup_source.charge(
                organization_id=1,
                source=lookup_source.LookupSource("platform", "hunter"),
                verified=3,
                ref_id="r",
                workflow_id=42,
            )
        kw = passthrough.await_args.kwargs
        assert kw["provider"] == "hunter"
        assert kw["kind"] == lookup_source.VERIFIED_EMAIL
        assert kw["requests"] == 3
        assert kw["ref_id"] == "r"
        assert out["verified"] == 3
        assert out["pass_through_paise"] == 480
        assert "note" not in out

    async def test_platform_with_nothing_verified_passes_nothing_through(self):
        with (
            patch("api.services.billing.events.charge_in_own_session", AsyncMock()),
            patch(
                "api.services.billing.data_costs.debit_lookup_in_own_session",
                AsyncMock(),
            ) as passthrough,
        ):
            await lookup_source.charge(
                organization_id=1,
                source=lookup_source.LookupSource("platform", "hunter"),
                verified=0,
                ref_id="r",
            )
        passthrough.assert_not_awaited()

    async def test_a_missing_rate_is_said_on_the_result(self):
        with (
            patch("api.services.billing.events.charge_in_own_session", AsyncMock()),
            patch(
                "api.services.billing.data_costs.debit_lookup_in_own_session",
                AsyncMock(return_value=0),
            ),
        ):
            out = await lookup_source.charge(
                organization_id=1,
                source=lookup_source.LookupSource("platform", "hunter"),
                verified=2,
                ref_id="r",
            )
        assert "No rate on file for hunter" in out["note"]

    async def test_no_source_charges_nothing(self):
        with patch(
            "api.services.billing.events.charge_in_own_session", AsyncMock()
        ) as fee:
            out = await lookup_source.charge(
                organization_id=1,
                source=lookup_source.LookupSource("none"),
                verified=9,
                ref_id="r",
            )
        fee.assert_not_awaited()
        assert out["charged"] is False
