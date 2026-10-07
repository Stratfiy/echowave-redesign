"""Treg as the lead source: priced first, capped, metered, honest when it stops.

Treg (https://treg.to) is one token over many lead-data vendors. The
responses below are recorded shapes from its documentation
(https://treg.to/llms.txt, the ``treg.people.search`` /
``treg.people.email.find`` / ``treg.people.email.verify`` catalog entries):
the body is ``{output, raw, _treg}`` and the charge is the
``X-Treg-Cost-Micro`` header. Treg is never called for real here.

What must hold:

* A search is priced on the thread before anything is spent, and runs only
  after the person has answered that price.
* Every call carries the token, an ``Idempotency-Key`` from the run (a retry
  replays, never pays twice), ``X-Treg-Route-Max-Cost`` no higher than what
  is left of the run's cap, and excludes the LinkedIn scraper.
* Only addresses Treg verified become leads.
* What Treg charged is recorded, call by call, in vendor metering.
* Out of balance, rate limited, over the cap, provider out, token rejected,
  no token: each is its own state, never an empty list.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.db.models import ModelUsageModel, OrganizationModel
from api.enums import AgentEventActor, AgentEventKind, CostComponent
from api.services.configuration import organization_credentials
from api.services.outreach import leads
from api.services.outreach import tools as outreach

# --- recorded responses ----------------------------------------------------------

SEARCH = {
    "output": {
        "people": [
            # One provider's shape: email present, unverified.
            {
                "full_name": "Asha Rao",
                "title": "Owner",
                "company_name": "Lotus Dental",
                "company_domain": "lotusdental.example.com",
                "email": "asha@lotusdental.example.com",
                "location": "Pune, India",
                "linkedin_url": "https://www.linkedin.com/in/asha-rao-example",
            },
            # Another provider's shape: no email, a domain to find one at.
            {
                "first_name": "Vikram",
                "last_name": "Shah",
                "job_title": "Practice Manager",
                "organization": {"name": "Smile Studio"},
                "domain": "smilestudio.example.org",
                "city": "Pune",
            },
            # A catch-all mailbox: risky, never a lead.
            {
                "name": "Neha Iyer",
                "title": "Founder",
                "company": "Bright Teeth",
                "email": "neha@brightteeth.example.net",
            },
        ],
        "count": 3,
        "next_cursor": None,
    },
    "raw": {},
    "_treg": {
        "served_by": "quickenrich.people.search",
        "tried": ["quickenrich.people.search"],
    },
}
FIND = {
    "output": {"email": "vikram@smilestudio.example.org", "verified": False},
    "raw": {},
    "_treg": {"served_by": "icypeas.people.email.find", "tried": []},
}
VERIFY = {
    "asha@lotusdental.example.com": {"valid": True, "status": "valid", "score": 97},
    "vikram@smilestudio.example.org": {"valid": True, "status": "valid", "score": 91},
    "neha@brightteeth.example.net": {
        "valid": True,
        "status": "accept_all",
        "score": 60,
    },
}
COST = {
    "treg.people.search": 2634,
    "treg.people.email.find": 9800,
    "treg.people.email.verify": 6000,
}


class FakeTreg:
    """treg.to/call/<endpoint>, answering from the recorded shapes."""

    def __init__(
        self, failure: tuple[int, dict, dict] | None = None, fail_on: str = ""
    ):
        self.requests: list[httpx.Request] = []
        self.failure = failure
        self.fail_on = fail_on

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        endpoint = request.url.path.removeprefix("/call/")
        if self.failure and (not self.fail_on or endpoint == self.fail_on):
            status, body, headers = self.failure
            return httpx.Response(status, json=body, headers=headers)
        if endpoint == "treg.people.search":
            body = SEARCH
        elif endpoint == "treg.people.email.find":
            body = FIND
        elif endpoint == "treg.people.email.verify":
            email = json.loads(request.content)["email"]
            body = {
                "output": VERIFY[email],
                "raw": {},
                "_treg": {"served_by": "zerobounce"},
            }
        else:
            return httpx.Response(404, json={"detail": "unknown endpoint"})
        return httpx.Response(
            200,
            json=body,
            headers={
                "X-Treg-Cost-Micro": str(COST[endpoint]),
                "X-Treg-Call-Id": f"call_{len(self.requests)}",
            },
        )

    def patch(self):
        real = httpx.AsyncClient

        def client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(self.handler)
            return real(*args, **kwargs)

        return patch.object(leads.httpx, "AsyncClient", client)


# --- the provider on its own ----------------------------------------------------


class TestTregSearch:
    async def test_only_verified_addresses_become_leads(self):
        fake = FakeTreg()
        budget = leads.Budget(cap_micro=500_000)
        with fake.patch():
            result = await leads.Treg().search(
                "tok", leads.Criteria(titles=("Owner",), locations=("Pune",)), budget
            )
        assert [lead.name for lead in result.leads] == ["Asha Rao", "Vikram Shah"]
        assert result.leads[1].email == "vikram@smilestudio.example.org"
        # The catch-all address is counted, not offered.
        assert result.without_email == 1
        # No profile URL is kept from a row that carried one.
        assert "linkedin" not in json.dumps([lead.as_dict() for lead in result.leads])
        assert (
            budget.spent_micro
            == COST["treg.people.search"]
            + COST["treg.people.email.find"]
            + 3 * COST["treg.people.email.verify"]
        )

    async def test_every_call_carries_token_key_cap_and_the_linkedin_exclusion(self):
        fake = FakeTreg()
        budget = leads.Budget(cap_micro=500_000, run_id="run-1")
        with fake.patch():
            await leads.Treg().search(
                "tok-123", leads.Criteria(titles=("Owner",)), budget
            )
        keys = set()
        for request in fake.requests:
            assert request.headers["X-Treg-Token"] == "tok-123"
            assert "tok-123" not in str(request.url)
            assert request.headers["X-Treg-Route-Exclude"] == "harvestapi"
            assert float(request.headers["X-Treg-Route-Max-Cost"]) <= 0.5
            keys.add(request.headers["Idempotency-Key"])
            assert request.headers["Idempotency-Key"].startswith("decibyl-run-1-")
        assert len(keys) == len(fake.requests)  # one key per call

    async def test_a_retried_run_sends_the_same_keys(self):
        """The same run again (a retried turn) replays at Treg, not re-bills."""
        first, second = FakeTreg(), FakeTreg()
        criteria = leads.Criteria(titles=("Owner",))
        with first.patch():
            await leads.Treg().search("t", criteria, leads.Budget(500_000, run_id="r"))
        with second.patch():
            await leads.Treg().search("t", criteria, leads.Budget(500_000, run_id="r"))
        assert [r.headers["Idempotency-Key"] for r in first.requests] == [
            r.headers["Idempotency-Key"] for r in second.requests
        ]

    async def test_the_cap_is_never_offered_past_what_is_left(self):
        fake = FakeTreg()
        budget = leads.Budget(cap_micro=20_000)
        with fake.patch():
            result = await leads.Treg().search(
                "t", leads.Criteria(titles=("Owner",)), budget
            )
        caps = [
            float(r.headers["X-Treg-Route-Max-Cost"]) * 1_000_000 for r in fake.requests
        ]
        spent = 0
        for request, cap in zip(fake.requests, caps):
            assert cap <= 20_000 - spent + 1
            spent += COST[request.url.path.removeprefix("/call/")]
        assert budget.spent_micro <= 20_000 + COST["treg.people.email.verify"]
        assert result.stopped is not None or len(result.leads) >= 1

    def test_the_estimate_is_said_in_dollars_under_the_cap(self, monkeypatch):
        monkeypatch.setattr(constants, "LEAD_SEARCH_MAX_USD", "0.40")
        estimate = leads.Treg().estimate(leads.Criteria(titles=("Owner",), limit=10))
        assert estimate.unit == "USD"
        assert 0 < estimate.typical_usd <= estimate.cap_usd == 0.40


# --- the tool, in a workspace ------------------------------------------------------


async def _org() -> int:
    async with db_client.async_session() as session:
        org = OrganizationModel(provider_id=f"treg-{uuid4().hex}")
        session.add(org)
        await session.flush()
        organization_id = int(org.id)
        await session.commit()
    return organization_id


@pytest.fixture
async def org(test_engine, monkeypatch):
    monkeypatch.setattr(constants, "OUTREACH_ENABLED", True)
    monkeypatch.setattr(constants, "LEAD_DATA_PROVIDER", "treg")
    monkeypatch.setattr(constants, "VENDOR_METERING_2026_09_ENABLED", True)
    organization_id = await _org()
    yield organization_id
    async with db_client.async_session() as session:
        for table in (
            "agent_events",
            "organization_provider_credentials",
            "model_usage",
        ):
            await session.execute(
                text(f"DELETE FROM {table} WHERE organization_id = :o"),
                {"o": organization_id},
            )
        await session.commit()


async def _token(org: int, value: str = "treg-token-abcd") -> None:
    async with db_client.async_session() as session:
        await organization_credentials.set_credential(
            session,
            organization_id=org,
            actor_user_id=None,
            component=CostComponent.DATA,
            provider="treg",
            api_key=value,
        )
        await session.commit()


async def _say(org: int, words: str) -> None:
    from api.services.workflow import agent_timeline

    await agent_timeline.record(
        organization_id=org,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.HUMAN.value,
        summary=words,
        payload={"body": words},
        in_channel=False,
    )


ASK = {
    "titles": ["Clinic owner"],
    "locations": ["Pune"],
    "industries": ["dental"],
    "limit": 5,
}


def _no_charge():
    return patch(
        "api.services.billing.lookup_source.charge",
        AsyncMock(return_value={"charged": True}),
    )


@pytest.mark.asyncio
class TestPricedFirst:
    async def test_the_first_call_prices_and_spends_nothing(self, org):
        await _token(org)
        fake = FakeTreg()
        with fake.patch():
            out = await outreach.find(org, ASK)
        assert out["status"] == "estimate"
        assert out["estimate"]["unit"] == "USD" and out["estimate"]["at_most"] > 0
        assert fake.requests == []
        rows = await db_client.agent_events(
            organization_id=org,
            kinds=[AgentEventKind.ACTIVITY.value],
            assistant_thread=True,
        )
        assert any("Nothing spent yet" in (r.summary or "") for r in rows)

    async def test_asking_again_without_an_answer_does_not_run(self, org):
        """The model cannot agree to the price for the person."""
        await _token(org)
        fake = FakeTreg()
        with fake.patch():
            await outreach.find(org, ASK)
            again = await outreach.find(org, ASK)
        assert again["status"] == "estimate"
        assert fake.requests == []

    async def test_after_the_person_answers_it_runs_once_and_is_metered(self, org):
        await _token(org)
        fake = FakeTreg()
        with fake.patch(), _no_charge():
            await outreach.find(org, ASK)
            await _say(org, "Yes, run it")
            out = await outreach.find(org, ASK)
            # The same search again is a new price, not a second free run.
            repeat = await outreach.find(org, ASK)
        assert out["status"] == "success"
        assert [lead["name"] for lead in out["leads"]] == ["Asha Rao", "Vikram Shah"]
        spent = sum(COST[r.url.path.removeprefix("/call/")] for r in fake.requests)
        assert out["spent_usd"] == pytest.approx(spent / 1_000_000)
        assert out["spent_usd"] <= out["cap_usd"]
        assert repeat["status"] == "estimate"
        async with db_client.async_session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT provider, model, unit, quantity, feature FROM model_usage "
                        "WHERE organization_id = :o"
                    ),
                    {"o": org},
                )
            ).all()
        assert {r.provider for r in rows} == {"treg"}
        assert {r.unit for r in rows} == {"usd_micro"}
        assert {r.feature for r in rows} == {"outreach"}
        assert sum(r.quantity for r in rows) == spent
        assert ModelUsageModel.__tablename__ == "model_usage"


@pytest.mark.asyncio
class TestEveryStopIsAState:
    async def _run(self, org, fake):
        await _token(org)
        with fake.patch(), _no_charge():
            await outreach.find(org, ASK)
            await _say(org, "go")
            return await outreach.find(org, ASK)

    async def test_out_of_balance_gives_the_top_up_link(self, org):
        out = await self._run(
            org,
            FakeTreg(
                (
                    402,
                    {
                        "error": "insufficient_balance",
                        "balance_micro": 1200,
                        "estimated_cost_micro": 26340,
                        "topup_url": "https://treg.to/billing/topup?org=example",
                    },
                    {},
                )
            ),
        )
        assert out["status"] == "out_of_balance"
        assert out["topup_url"] == "https://treg.to/billing/topup?org=example"
        assert "leads" not in out

    async def test_rate_limited_says_try_again(self, org):
        out = await self._run(
            org, FakeTreg((429, {"detail": "slow down"}, {"Retry-After": "30"}))
        )
        assert out["status"] == "rate_limited"
        assert out["retry_after"] == "30"
        assert "leads" not in out

    async def test_over_the_cap_is_said_not_charged(self, org):
        out = await self._run(org, FakeTreg((402, {"error": "route_max_cost"}, {})))
        assert out["status"] == "cap_reached"
        assert out["spent_usd"] == 0

    async def test_provider_out_is_unavailable_not_your_balance(self, org):
        out = await self._run(
            org,
            FakeTreg(
                (
                    503,
                    {
                        "error": "provider_capacity_unavailable",
                        "resets_at": "2026-10-07T20:00:00Z",
                    },
                    {},
                )
            ),
        )
        assert out["status"] == "unavailable"
        assert "not your balance" in out["reason"]

    async def test_a_rejected_token_puts_a_new_form_on_the_thread(self, org):
        out = await self._run(org, FakeTreg((401, {"detail": "invalid token"}, {})))
        assert out["status"] == "key_rejected"
        forms = await db_client.agent_events(
            organization_id=org,
            kinds=[AgentEventKind.NEEDS_SECRET.value],
            assistant_thread=True,
        )
        assert forms and forms[0].payload["provider_key"]["provider"] == "treg"

    async def test_a_rate_limit_after_some_leads_returns_them_as_partial(self, org):
        out = await self._run(
            org,
            FakeTreg((429, {}, {}), fail_on="treg.people.email.find"),
        )
        assert out["status"] == "success"
        assert [lead["name"] for lead in out["leads"]] == ["Asha Rao"]
        assert "partial" in out

    async def test_no_token_is_needs_setup_with_a_treg_form(self, org):
        from api.services.configuration import platform_credentials

        fake = FakeTreg()
        with (
            fake.patch(),
            patch.object(
                platform_credentials, "resolve_api_key", AsyncMock(return_value=None)
            ),
        ):
            out = await outreach.find(org, ASK)
        assert out["status"] == "needs_setup" and out["provider"] == "Treg"
        assert fake.requests == []
        forms = await db_client.agent_events(
            organization_id=org,
            kinds=[AgentEventKind.NEEDS_SECRET.value],
            assistant_thread=True,
        )
        assert forms[0].payload["provider_key"] == {
            "component": "data",
            "provider": "treg",
            "label": "Treg",
        }


class TestTheTokenIsAProviderKey:
    def test_treg_is_a_data_provider(self):
        from api.services.configuration.registry import known_providers

        assert known_providers()["treg"] == ("data",)

    async def test_the_token_is_checked_against_the_balance(self):
        from api.services.configuration import key_validation

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/balance"
            ok = request.headers.get("X-Treg-Token") == "good"
            return httpx.Response(200 if ok else 401, json={"balance_micro": 1_000_000})

        real = httpx.AsyncClient

        def client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real(*args, **kwargs)

        with patch("httpx.AsyncClient", client):
            good = await key_validation.validate_key("treg", "good")
            bad = await key_validation.validate_key("treg", "bad")
        assert good.outcome == "valid"
        assert bad.outcome == "invalid" and not bad.may_store
