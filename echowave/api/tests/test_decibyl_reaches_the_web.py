"""D-1b: Decibyl's own tools -- the web, the workspace's records, the sandbox.

Behind ``DECIBYL_TOOLS_2026_09_ENABLED``. A search runs on the platform's
key and is charged as a tool call plus the vendor's price at cost; a fetch
honours robots, refuses the social networks and private addresses, is rate
limited per domain and cut to size; a records search reads only this
workspace and writes nothing; the sandbox is offered only on plans that
have it. Every tool is named in the rules, or the model uses it by guess.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select

from api import constants
from api.db.models import (
    CreditLedgerModel,
    DataLookupCostModel,
    OrganizationModel,
    ProviderRateModel,
)
from api.enums import CostComponent, RateUnit
from api.services.billing import data_costs
from api.services.workflow import decibyl, records, web_tools


@pytest.fixture
def tools_on(monkeypatch):
    monkeypatch.setattr(constants, "DECIBYL_TOOLS_2026_09_ENABLED", True)
    monkeypatch.setattr(constants, "CRAWL4AI_URL", "")
    monkeypatch.setattr(constants, "WEB_FETCH_MAX_CHARS", 12_000)


@pytest.fixture
def tools_off(monkeypatch):
    monkeypatch.setattr(constants, "DECIBYL_TOOLS_2026_09_ENABLED", False)


def _http(handler):
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )


class TestTheDrawer:
    def test_off_the_tools_are_not_offered(self, tools_off):
        names = {t["name"] for t in decibyl.office_tools()}
        assert web_tools.SEARCH_TOOL_NAME not in names
        assert records.TOOL_NAME not in names

    def test_on_they_are_and_every_one_is_in_the_rules(self, tools_on):
        names = {t["name"] for t in decibyl.office_tools()}
        assert {
            web_tools.SEARCH_TOOL_NAME,
            web_tools.FETCH_TOOL_NAME,
            records.TOOL_NAME,
        } <= names
        for t in decibyl.office_tools():
            assert t["name"] in decibyl.SYSTEM, t["name"]
        assert "run_script" in decibyl.SYSTEM

    @pytest.mark.asyncio
    async def test_the_sandbox_is_offered_only_on_a_plan_that_has_it(self, tools_on):
        with (
            patch(
                "api.services.workflow.connected_tools.list_for_organization",
                AsyncMock(return_value=[]),
            ),
            patch(
                "api.services.sandbox.code_mode.allowed", AsyncMock(return_value=False)
            ),
        ):
            assert "run_script" not in {
                t["name"] for t in await decibyl.tools_for(7, {})
            }
        with (
            patch(
                "api.services.workflow.connected_tools.list_for_organization",
                AsyncMock(return_value=[]),
            ),
            patch(
                "api.services.sandbox.code_mode.allowed", AsyncMock(return_value=True)
            ),
        ):
            assert "run_script" in {t["name"] for t in await decibyl.tools_for(7, {})}

    def test_each_is_a_read_that_keeps_the_tools(self, tools_on):
        for name in (
            web_tools.SEARCH_TOOL_NAME,
            web_tools.FETCH_TOOL_NAME,
            records.TOOL_NAME,
        ):
            call = SimpleNamespace(name=name, arguments={})
            assert decibyl._was_a_read(call, {"status": "success"})
            assert decibyl._was_a_read(call, {"status": "refused"})


@pytest.mark.asyncio
class TestSearch:
    async def test_it_asks_serper_and_charges_the_call_and_the_lookup(self, tools_on):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = dict(request.headers)
            seen["body"] = request.read()
            return httpx.Response(
                200,
                json={
                    "organic": [
                        {
                            "title": "Acme",
                            "link": "https://acme.example",
                            "snippet": "Widgets",
                        },
                    ],
                    "answerBox": {"answer": "42"},
                },
            )

        with (
            patch.object(web_tools, "_search_key", AsyncMock(return_value="k-1")),
            patch(
                "api.services.billing.events.charge_in_own_session",
                AsyncMock(return_value=50),
            ) as charge,
            patch(
                "api.services.billing.data_costs.debit_lookup_in_own_session",
                AsyncMock(return_value=10),
            ) as debit,
        ):
            async with _http(handler) as client:
                out = await web_tools.search(
                    7,
                    {"query": "acme widgets", "count": 3, "country": "in"},
                    ref_id="decibyl:7:c1",
                    client=client,
                )
        assert out["status"] == "success"
        assert out["results"][0]["link"] == "https://acme.example"
        assert out["answer"] == {"answer": "42"}
        assert seen["headers"]["x-api-key"] == "k-1"
        assert b'"gl": "in"' in seen["body"] or b'"gl":"in"' in seen["body"]
        assert charge.await_args.kwargs["ref_id"] == "decibyl:7:c1"
        assert debit.await_args.kwargs == {
            "organization_id": 7,
            "provider": "serper",
            "kind": "search",
            "requests": 1,
            "ref_id": "decibyl:7:c1",
            "workflow_id": None,
        }

    async def test_no_key_is_told_not_guessed(self, tools_on, monkeypatch):
        monkeypatch.setattr(constants, "SERPER_API_KEY", "")
        with patch.object(web_tools, "_search_key", AsyncMock(return_value=None)):
            out = await web_tools.search(7, {"query": "x"}, ref_id="r")
        assert out["status"] == "unavailable"
        assert "Serper" in out["reason"]

    async def test_a_vendor_error_charges_nothing(self, tools_on):
        def handler(request):
            return httpx.Response(429, json={})

        with (
            patch.object(web_tools, "_search_key", AsyncMock(return_value="k")),
            patch(
                "api.services.billing.events.charge_in_own_session", AsyncMock()
            ) as charge,
        ):
            async with _http(handler) as client:
                out = await web_tools.search(
                    7, {"query": "x"}, ref_id="r", client=client
                )
        assert out["status"] == "error"
        assert "429" in out["error"]
        charge.assert_not_awaited()


@pytest.mark.asyncio
class TestFetch:
    PAGE = (
        "<html><head><title>Acme &amp; Co</title><style>x{}</style></head>"
        "<body><script>var a=1;</script><h1>Prices</h1><p>Widgets cost  ₹5.</p>"
        "<ul><li>Small</li><li>Large</li></ul></body></html>"
    )

    def _handler(self, robots: str = "", status: int = 200):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(200 if robots else 404, text=robots)
            return httpx.Response(
                status, text=self.PAGE, headers={"content-type": "text/html"}
            )

        return handler

    async def test_a_page_comes_back_as_text(self, tools_on):
        with (
            patch.object(
                web_tools, "_domain_allowed", AsyncMock(return_value=(True, 0))
            ),
            patch(
                "api.services.billing.events.charge_in_own_session", AsyncMock()
            ) as charge,
        ):
            async with _http(self._handler()) as client:
                out = await web_tools.fetch(
                    7, {"url": "https://acme.example/prices"}, ref_id="r", client=client
                )
        assert out["status"] == "success"
        assert out["title"] == "Acme & Co"
        assert "Prices\nWidgets cost ₹5.\nSmall\nLarge" in out["text"]
        assert "var a=1" not in out["text"]
        assert charge.await_args.kwargs["note"] == "web fetch: acme.example"

    async def test_robots_is_honoured(self, tools_on):
        robots = "User-agent: *\nDisallow: /private\n"
        with (
            patch.object(
                web_tools, "_domain_allowed", AsyncMock(return_value=(True, 0))
            ),
            patch(
                "api.services.billing.events.charge_in_own_session", AsyncMock()
            ) as charge,
        ):
            async with _http(self._handler(robots)) as client:
                blocked = await web_tools.fetch(
                    7,
                    {"url": "https://acme.example/private/x"},
                    ref_id="r",
                    client=client,
                )
                fine = await web_tools.fetch(
                    7,
                    {"url": "https://acme.example/public"},
                    ref_id="r2",
                    client=client,
                )
        assert blocked["status"] == "refused"
        assert "robots" in blocked["reason"]
        assert fine["status"] == "success"
        assert charge.await_count == 1

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.linkedin.com/in/someone",
            "https://x.com/someone",
            "https://m.facebook.com/page",
            "http://localhost:8000/admin",
            "http://127.0.0.1/",
            "http://10.0.0.5/secret",
            "ftp://acme.example/file",
            "not a url",
        ],
    )
    async def test_the_social_networks_and_private_addresses_are_refused(
        self, tools_on, url
    ):
        with patch(
            "api.services.billing.events.charge_in_own_session", AsyncMock()
        ) as charge:
            out = await web_tools.fetch(7, {"url": url}, ref_id="r")
        assert out["status"] == "refused", url
        charge.assert_not_awaited()

    async def test_a_domain_is_read_only_so_often(self, tools_on):
        with (
            patch.object(
                web_tools, "_domain_allowed", AsyncMock(return_value=(False, 37))
            ),
            patch(
                "api.services.billing.events.charge_in_own_session", AsyncMock()
            ) as charge,
        ):
            out = await web_tools.fetch(
                7, {"url": "https://acme.example/a"}, ref_id="r"
            )
        assert out["status"] == "refused"
        assert "37s" in out["reason"]
        charge.assert_not_awaited()

    async def test_the_text_is_cut_to_size(self, tools_on, monkeypatch):
        monkeypatch.setattr(constants, "WEB_FETCH_MAX_CHARS", 20)
        with (
            patch.object(
                web_tools, "_domain_allowed", AsyncMock(return_value=(True, 0))
            ),
            patch("api.services.billing.events.charge_in_own_session", AsyncMock()),
        ):
            async with _http(self._handler()) as client:
                out = await web_tools.fetch(
                    7, {"url": "https://acme.example/p"}, ref_id="r", client=client
                )
        assert len(out["text"]) == 20
        assert "Cut at 20" in out["note"]

    async def test_a_dead_page_is_an_error_not_a_charge(self, tools_on):
        with (
            patch.object(
                web_tools, "_domain_allowed", AsyncMock(return_value=(True, 0))
            ),
            patch(
                "api.services.billing.events.charge_in_own_session", AsyncMock()
            ) as charge,
        ):
            async with _http(self._handler(status=503)) as client:
                out = await web_tools.fetch(
                    7, {"url": "https://acme.example/p"}, ref_id="r", client=client
                )
        assert out["status"] == "error"
        assert "503" in out["error"]
        charge.assert_not_awaited()


@pytest.mark.asyncio
class TestRecords:
    async def test_contacts_by_name_or_number(self):
        rows = [
            SimpleNamespace(
                name="Ravi",
                phone_normalized="+919876543210",
                phone_raw="98765 43210",
                attributes={"city": "Pune", "tags": ["a"]},
            ),
        ]
        with patch(
            "api.services.workflow.records.db_client.search_contacts_for_organization",
            AsyncMock(return_value=rows),
        ) as search:
            out = await records.for_thread(
                7, {"kind": "contacts", "query": "Ravi Pune"}
            )
        assert out["status"] == "success"
        assert out["rows"] == [
            {"name": "Ravi", "phone": "+919876543210", "attributes": {"city": "Pune"}}
        ]
        assert search.await_args.args[0] == 7
        assert search.await_args.args[1] == ["Ravi", "Pune"]

    async def test_contacts_need_a_name_or_a_number(self):
        out = await records.for_thread(7, {"kind": "contacts", "query": ""})
        assert out["status"] == "error"

    async def test_documents_by_file_name(self):
        rows = [
            SimpleNamespace(
                filename="Price list 2026.pdf",
                document_uuid="d-1",
                status="ready",
                created_at=datetime(2026, 9, 1, tzinfo=UTC),
            ),
            SimpleNamespace(
                filename="Brochure.pdf",
                document_uuid="d-2",
                status="ready",
                created_at=None,
            ),
        ]
        with patch(
            "api.services.workflow.records.db_client.get_documents_for_organization",
            AsyncMock(return_value=rows),
        ):
            out = await records.for_thread(7, {"kind": "documents", "query": "price"})
        assert [r["name"] for r in out["rows"]] == ["Price list 2026.pdf"]

    async def test_outcomes_in_a_window(self):
        now = datetime.now(UTC)
        rows = [
            SimpleNamespace(
                at=now - timedelta(days=2),
                kind="outcome_filed",
                workflow_id=3,
                workflow_run_id=9,
                summary="Booked a slot for Meera",
            ),
            SimpleNamespace(
                at=now - timedelta(days=40),
                kind="outcome_filed",
                workflow_id=3,
                workflow_run_id=8,
                summary="Booked a slot for Ravi",
            ),
        ]
        with patch(
            "api.services.workflow.records.db_client.agent_events",
            AsyncMock(return_value=rows),
        ) as events:
            out = await records.for_thread(
                7, {"kind": "outcomes", "query": "booked", "days": 30}
            )
        assert [r["run_id"] for r in out["rows"]] == [9]
        assert events.await_args.kwargs["kinds"] == ["outcome_filed"]
        assert events.await_args.kwargs["organization_id"] == 7

    async def test_an_unknown_book_is_refused(self):
        out = await records.for_thread(7, {"kind": "secrets"})
        assert out["status"] == "error"


@pytest.mark.asyncio
class TestTheLookupIsMetered:
    async def test_a_search_debits_the_vendor_price_at_cost_once(self, async_session):
        org = OrganizationModel(provider_id="org-lookup", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
        async_session.add(
            ProviderRateModel(
                provider="serper",
                model="search",
                component=CostComponent.DATA.value,
                unit=RateUnit.EACH.value,
                rate_mpaise=9_600,  # $0.001 at ₹96
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        await async_session.flush()
        first = await data_costs.debit_lookup(
            async_session,
            organization_id=org.id,
            provider="serper",
            kind="search",
            requests=3,
            ref_id="decibyl:7:c1",
        )
        again = await data_costs.debit_lookup(
            async_session,
            organization_id=org.id,
            provider="serper",
            kind="search",
            requests=3,
            ref_id="decibyl:7:c1",
        )
        # 3 x 9,600 mpaise = 28.8 paise -> 29, at cost (data markup is 1.0)
        assert first == 29
        assert again == 0
        rows = (
            await async_session.scalars(
                select(DataLookupCostModel).where(
                    DataLookupCostModel.organization_id == org.id
                )
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].vendor_cost_paise == 29 and rows[0].charged_paise == 29
        ledger = (
            await async_session.scalars(
                select(CreditLedgerModel).where(
                    CreditLedgerModel.organization_id == org.id
                )
            )
        ).all()
        assert [r.delta_paise for r in ledger] == [-29]
        assert ledger[0].ref_type == data_costs.REF_TYPE

    async def test_no_rate_debits_nothing_and_says_so(self, async_session):
        org = OrganizationModel(provider_id="org-norate", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
        charged = await data_costs.debit_lookup(
            async_session,
            organization_id=org.id,
            provider="nobody",
            kind="x",
            requests=1,
            ref_id="r",
        )
        assert charged == 0
