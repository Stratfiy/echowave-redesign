"""OP-2: the fetch is bounded per run, kept to an agent's list, and hands
back what was asked for.

A run reads at most its cap of pages and is told when it has; an agent
whose web tool names sites reads and searches those only; a fetch that
says what it is looking for gets the matching parts first; every page
comes back with the addresses and numbers it shows.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from api import constants
from api.services.workflow import agent_web, web_tools

PAGE = (
    "<html><head><title>Sunrise Dental</title></head><body>"
    "<h1>Sunrise Dental Clinic</h1>"
    "<p>Family dentistry in Anna Nagar since 2009.</p>"
    "<h2>Opening hours</h2><p>Mon to Sat, 9am to 8pm. Closed Sunday.</p>"
    "<h2>Contact</h2><p>Call +91 44 2345 6789 or write to hello@sunrisedental.example</p>"
    "<p>Owner: Dr. Priya Raman</p>"
    "<img src='logo@2x.png'>"
    "</body></html>"
)


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(constants, "DECIBYL_TOOLS_2026_09_ENABLED", True)
    monkeypatch.setattr(constants, "CRAWL4AI_URL", "")
    monkeypatch.setattr(constants, "WEB_FETCH_MAX_CHARS", 12_000)
    monkeypatch.setattr(constants, "WEB_FETCH_MAX_PAGES_PER_RUN", 25)


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/robots.txt":
        return httpx.Response(404)
    return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})


def _http():
    return httpx.AsyncClient(transport=httpx.MockTransport(_handler))


def _no_domain_limit():
    return patch.object(web_tools, "_domain_allowed", AsyncMock(return_value=(True, 0)))


def _no_charge():
    return patch("api.services.billing.events.charge_in_own_session", AsyncMock())


class TestThePageCap:
    async def test_a_run_reads_its_cap_then_is_told(self):
        counter = AsyncMock(side_effect=[(True, 0), (True, 0), (False, 3600)])
        with (
            _no_domain_limit(),
            _no_charge() as charge,
            patch("api.services.rate_limit.rate_limiter.check", counter),
        ):
            async with _http() as client:
                outs = [
                    await web_tools.fetch(
                        7,
                        {"url": f"https://sunrisedental.example/p{i}"},
                        ref_id=f"r{i}",
                        run_key="run:9",
                        max_pages=2,
                        client=client,
                    )
                    for i in range(3)
                ]
        assert [o["status"] for o in outs] == ["success", "success", "refused"]
        assert "read its 2 pages" in outs[2]["reason"]
        assert charge.await_count == 2
        kwargs = counter.await_args.kwargs
        assert kwargs["bucket"] == web_tools.RUN_BUCKET
        assert kwargs["identity"] == "run:9"
        assert kwargs["limit"] == 2

    async def test_the_platform_cap_stands_when_the_tool_sets_none(self, monkeypatch):
        monkeypatch.setattr(constants, "WEB_FETCH_MAX_PAGES_PER_RUN", 4)
        counter = AsyncMock(return_value=(True, 0))
        with (
            _no_domain_limit(),
            _no_charge(),
            patch("api.services.rate_limit.rate_limiter.check", counter),
        ):
            async with _http() as client:
                await web_tools.fetch(
                    7,
                    {"url": "https://sunrisedental.example/"},
                    ref_id="r",
                    run_key="run:9",
                    client=client,
                )
        assert counter.await_args.kwargs["limit"] == 4

    async def test_a_refused_page_is_not_counted_before_its_own_rules(self):
        """The list and the address rules come first: a refusal for LinkedIn
        does not spend one of the run's pages."""
        counter = AsyncMock(return_value=(True, 0))
        with patch("api.services.rate_limit.rate_limiter.check", counter):
            out = await web_tools.fetch(
                7,
                {"url": "https://www.linkedin.com/in/x"},
                ref_id="r",
                run_key="run:9",
            )
        assert out["status"] == "refused"
        counter.assert_not_awaited()


class TestTheAllowList:
    def test_domains_are_normalised(self):
        assert web_tools.normalise_domains(
            ["https://www.Acme.example/about", "acme.example", " beta.example. ", ""]
        ) == ["acme.example", "beta.example"]

    async def test_a_page_off_the_list_is_refused_and_not_read(self):
        with _no_domain_limit(), _no_charge() as charge:
            out = await web_tools.fetch(
                7,
                {"url": "https://other.example/x"},
                ref_id="r",
                allowed_domains=["sunrisedental.example"],
            )
        assert out["status"] == "refused"
        assert "sunrisedental.example" in out["reason"]
        charge.assert_not_awaited()

    async def test_a_subdomain_of_a_listed_site_is_on_the_list(self):
        with _no_domain_limit(), _no_charge():
            async with _http() as client:
                out = await web_tools.fetch(
                    7,
                    {"url": "https://blog.sunrisedental.example/x"},
                    ref_id="r",
                    allowed_domains=["sunrisedental.example"],
                    client=client,
                )
        assert out["status"] == "success"

    async def test_a_search_is_narrowed_to_the_list_and_strays_dropped(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["q"] = request.read().decode()
            return httpx.Response(
                200,
                json={
                    "organic": [
                        {"title": "A", "link": "https://sunrisedental.example/a"},
                        {"title": "B", "link": "https://elsewhere.example/b"},
                        {"title": "C", "link": "https://www.other.example/c"},
                    ]
                },
            )

        with (
            patch.object(web_tools, "_search_key", AsyncMock(return_value="k")),
            _no_charge(),
            patch(
                "api.services.billing.data_costs.debit_lookup_in_own_session",
                AsyncMock(),
            ),
        ):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
                out = await web_tools.search(
                    7,
                    {"query": "dentist hours"},
                    ref_id="r",
                    allowed_domains=["sunrisedental.example", "other.example"],
                    client=c,
                )
        assert "site:sunrisedental.example OR site:other.example" in seen["q"]
        assert [r["link"] for r in out["results"]] == [
            "https://sunrisedental.example/a",
            "https://www.other.example/c",
        ]


class TestWhatComesBack:
    async def test_looking_for_puts_the_matching_lines_first(self, monkeypatch):
        monkeypatch.setattr(constants, "WEB_FETCH_MAX_CHARS", 80)
        with _no_domain_limit(), _no_charge():
            async with _http() as client:
                out = await web_tools.fetch(
                    7,
                    {
                        "url": "https://sunrisedental.example/",
                        "looking_for": "opening hours",
                    },
                    ref_id="r",
                    client=client,
                )
        assert out["status"] == "success"
        assert "Mon to Sat, 9am to 8pm" in out["text"]
        assert "Anna Nagar" not in out["text"]
        assert out["looking_for"] == "opening hours"
        assert "speak to 'opening hours'" in out["note"]

    async def test_no_match_falls_back_to_the_top_and_says_so(self):
        with _no_domain_limit(), _no_charge():
            async with _http() as client:
                out = await web_tools.fetch(
                    7,
                    {"url": "https://sunrisedental.example/", "looking_for": "parking"},
                    ref_id="r",
                    client=client,
                )
        assert out["text"].startswith("Sunrise Dental Clinic")
        assert "Nothing on the page matches 'parking'" in out["note"]

    async def test_the_addresses_and_numbers_on_the_page_come_with_it(self):
        with _no_domain_limit(), _no_charge():
            async with _http() as client:
                out = await web_tools.fetch(
                    7,
                    {"url": "https://sunrisedental.example/"},
                    ref_id="r",
                    client=client,
                )
        assert out["found"] == {
            "emails": ["hello@sunrisedental.example"],
            "phones": ["+91 44 2345 6789"],
        }

    def test_found_on_skips_image_names_and_short_numbers(self):
        text = "logo@2x.png ref 12345 tel 98765 43210 mail sales@acme.example."
        assert web_tools.found_on(text) == {
            "emails": ["sales@acme.example"],
            "phones": ["98765 43210"],
        }

    def test_focus_keeps_a_heading_with_its_paragraph(self):
        text = "Home\nAbout us\nPricing\nWidgets cost 5\nContact\nMail us"
        out, matched = web_tools.focus(text, "pricing", 100)
        assert matched == 1
        assert out == "About us\nPricing\nWidgets cost 5"


class TestTheAgentsToolCarriesItsLimits:
    def test_limits_read_from_the_row(self):
        row = SimpleNamespace(
            definition={
                "type": "web",
                "config": {
                    "allowed_domains": ["acme.example"],
                    "max_pages_per_run": "5",
                },
            }
        )
        assert agent_web.limits_of(row) == {
            "allowed_domains": ["acme.example"],
            "max_pages": 5,
        }
        assert agent_web.limits_of(SimpleNamespace(definition={"type": "web"})) == {
            "allowed_domains": [],
            "max_pages": None,
        }
        assert agent_web.limits_of(None) == {"allowed_domains": [], "max_pages": None}

    def test_the_request_schema_normalises_and_refuses_junk(self):
        from pydantic import ValidationError

        from api.schemas.tool import WebToolDefinition

        d = WebToolDefinition.model_validate(
            {
                "type": "web",
                "config": {
                    "allowed_domains": ["https://www.Acme.example/x", "acme.example"],
                    "max_pages_per_run": 10,
                },
            }
        )
        assert d.config.allowed_domains == ["acme.example"]
        with pytest.raises(ValidationError):
            WebToolDefinition.model_validate(
                {"type": "web", "config": {"allowed_domains": ["*.acme.example"]}}
            )
        with pytest.raises(ValidationError):
            WebToolDefinition.model_validate(
                {"type": "web", "config": {"max_pages_per_run": 0}}
            )

    async def test_the_engine_hands_the_limits_and_the_run_to_every_call(self):
        from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager

        engine = SimpleNamespace(
            _is_voice=False,
            _workflow_run_id=7,
            llm=SimpleNamespace(register_function=None),
            _get_organization_id=AsyncMock(return_value=1),
        )
        registered: dict = {}
        engine.llm.register_function = lambda name, handler, **kw: (
            registered.__setitem__(name, handler)
        )
        manager = CustomToolManager(engine)
        manager._attribution = {
            "organization_id": 1,
            "workflow_id": 42,
            "workflow_run_id": 7,
            "workflow_version_id": None,
        }
        row = SimpleNamespace(
            tool_uuid="web-1",
            name="Web search",
            description="",
            category="web",
            definition={
                "type": "web",
                "config": {"allowed_domains": ["acme.example"], "max_pages_per_run": 3},
            },
        )
        manager._load_tools = AsyncMock(return_value=[row])
        await manager.register_handlers(["web-1"])

        results = []

        async def cb(result, *a, **k):
            results.append(result)

        fetch = AsyncMock(return_value={"status": "success"})
        search = AsyncMock(return_value={"status": "success"})
        with (
            patch("api.services.workflow.web_tools.fetch", fetch),
            patch("api.services.workflow.web_tools.search", search),
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.app_interactions._safe_record",
                AsyncMock(),
            ),
        ):
            await registered[web_tools.FETCH_TOOL_NAME](
                SimpleNamespace(
                    arguments={"url": "https://acme.example/"},
                    tool_call_id="c1",
                    result_callback=cb,
                    function_name=web_tools.FETCH_TOOL_NAME,
                )
            )
            await registered[web_tools.SEARCH_TOOL_NAME](
                SimpleNamespace(
                    arguments={"query": "q"},
                    tool_call_id="c2",
                    result_callback=cb,
                    function_name=web_tools.SEARCH_TOOL_NAME,
                )
            )
        f = fetch.call_args.kwargs
        assert f["run_key"] == "run:7"
        assert f["max_pages"] == 3
        assert f["allowed_domains"] == ["acme.example"]
        assert search.call_args.kwargs["allowed_domains"] == ["acme.example"]


class TestDecibylsThreadIsARun:
    async def test_a_fetch_from_a_thread_counts_against_that_thread(self):
        from api.services.workflow import decibyl

        fetch = AsyncMock(return_value={"status": "success"})
        call = SimpleNamespace(
            name=web_tools.FETCH_TOOL_NAME,
            id="c",
            arguments={"url": "https://a.example/"},
        )
        with patch("api.services.workflow.web_tools.fetch", fetch):
            await decibyl._tool(5, call, 11, request="", thread_id="t-9")
        assert fetch.call_args.kwargs["run_key"] == "thread:5:t-9"
