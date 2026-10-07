"""Outreach: leads from a provider, and an honest state without its key.

The slot (services/outreach/leads.py) is one interface with Apollo plugged
in. What must hold whatever provider is plugged in:

* No key is never an empty list. "Find me leads" with no key answers
  ``needs_setup``, names the provider, and puts a key form on the thread --
  once, not once per search.
* The key comes from the workspace's own provider keys first, then the
  platform's, and the key form stores into the same vault Settings uses.
* Only leads with a verified work address are offered; the rest are
  counted, not dropped silently.
* A key the provider rejects is said to be rejected, in words the owner
  can act on.

Apollo itself is never called: its two endpoints are answered by an
``httpx.MockTransport`` with the shapes its documentation gives.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.db.models import OrganizationModel
from api.enums import AgentEventKind, CostComponent
from api.services.configuration import organization_credentials
from api.services.outreach import leads
from api.services.outreach import tools as outreach

# --- a fake Apollo -------------------------------------------------------------

PEOPLE = [
    {
        "id": "p1",
        "first_name": "Asha",
        "last_name_obfuscated": "Ra***o",
        "title": "Owner",
        "organization": {"name": "Lotus Dental"},
    },
    {
        "id": "p2",
        "first_name": "Vikram",
        "title": "Practice Manager",
        "organization": {"name": "Smile Studio"},
    },
    {
        "id": "p3",
        "first_name": "Neha",
        "title": "Founder",
        "organization": {"name": "Bright Teeth"},
    },
]
MATCHES = {
    "p1": {
        "id": "p1",
        "name": "Asha Rao",
        "email": "asha@lotusdental.example.com",
        "email_status": "verified",
        "city": "Pune",
        "country": "India",
        "organization": {
            "name": "Lotus Dental",
            "website_url": "http://lotusdental.example.com",
            "industry": "hospital & health care",
            "estimated_num_employees": 18,
        },
    },
    "p2": {
        "id": "p2",
        "name": "Vikram Shah",
        "email": "vikram@smilestudio.example.com",
        "email_status": "verified",
        "organization": {"name": "Smile Studio"},
    },
    # A guessed address is not a lead to write to.
    "p3": {
        "id": "p3",
        "name": "Neha Iyer",
        "email": "neha@brightteeth.example.com",
        "email_status": "guessed",
        "organization": {"name": "Bright Teeth"},
    },
}


def _apollo(status: int = 200, body: dict | None = None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status != 200:
            return httpx.Response(status, json=body or {"error": "nope"})
        if request.url.path.endswith("/mixed_people/api_search"):
            return httpx.Response(200, json={"people": PEOPLE, "total_entries": 1234})
        if request.url.path.endswith("/people/bulk_match"):
            ids = [d["id"] for d in json.loads(request.content)["details"]]
            return httpx.Response(
                200, json={"matches": [MATCHES[i] for i in ids if i in MATCHES]}
            )
        return httpx.Response(404, json={"error": "unknown path"})

    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    return patch.object(leads.httpx, "AsyncClient", client), seen


# --- the provider --------------------------------------------------------------


class TestApollo:
    async def test_only_verified_addresses_are_leads(self):
        fake, seen = _apollo()
        with fake:
            result = await leads.Apollo().search(
                "k-123", leads.Criteria(titles=("Owner",), locations=("Pune",))
            )
        names = [lead.name for lead in result.leads]
        assert names == ["Asha Rao", "Vikram Shah"]
        assert result.without_email == 1
        assert result.total == 1234
        asha = result.leads[0]
        assert asha.email == "asha@lotusdental.example.com"
        assert asha.company == "Lotus Dental"
        assert asha.city == "Pune"
        # The key travels in Apollo's header, never in the URL.
        assert all(r.headers["x-api-key"] == "k-123" for r in seen)
        assert all("k-123" not in str(r.url) for r in seen)

    async def test_the_criteria_reach_apollo_in_its_own_terms(self):
        fake, seen = _apollo()
        with fake:
            await leads.Apollo().search(
                "k",
                leads.Criteria(
                    titles=("Clinic owner",),
                    locations=("Pune, India",),
                    industries=("dental",),
                    keywords="implants",
                    company_sizes=("11-50", "5001+"),
                ),
            )
        body = json.loads(seen[0].content)
        assert body["person_titles"] == ["Clinic owner"]
        assert body["person_locations"] == ["Pune, India"]
        assert body["organization_num_employees_ranges"] == ["11,50", "5001,1000000"]
        assert "implants" in body["q_keywords"] and "dental" in body["q_keywords"]

    async def test_a_rejected_key_says_so(self):
        fake, _ = _apollo(401, {"error": "Invalid access credentials."})
        with fake, pytest.raises(leads.LeadError) as caught:
            await leads.Apollo().search("bad", leads.Criteria(titles=("Owner",)))
        assert caught.value.code == "key_rejected"
        assert "rejected" in str(caught.value).lower()

    async def test_a_non_master_key_is_named(self):
        fake, _ = _apollo(
            403,
            {
                "error": "api/v1/mixed_people/api_search is not accessible with this api_key. Please use a master API key"
            },
        )
        with fake, pytest.raises(leads.LeadError) as caught:
            await leads.Apollo().search("k", leads.Criteria(titles=("Owner",)))
        assert "master" in str(caught.value).lower()

    def test_an_unknown_provider_setting_falls_back_loudly(self, monkeypatch):
        monkeypatch.setattr(constants, "LEAD_DATA_PROVIDER", "treg")
        assert leads.provider().name == "apollo"


# --- the tool, against the vault -----------------------------------------------


async def _org() -> int:
    async with db_client.async_session() as session:
        org = OrganizationModel(provider_id=f"outreach-{uuid4().hex}")
        session.add(org)
        await session.flush()
        organization_id = int(org.id)
        await session.commit()
    return organization_id


@pytest.fixture
async def org(test_engine, monkeypatch):
    monkeypatch.setattr(constants, "OUTREACH_ENABLED", True)
    organization_id = await _org()
    yield organization_id
    async with db_client.async_session() as session:
        for table in ("agent_events", "organization_provider_credentials"):
            await session.execute(
                text(f"DELETE FROM {table} WHERE organization_id = :o"),
                {"o": organization_id},
            )
        await session.commit()


async def _key_forms(organization_id: int) -> list:
    return await db_client.agent_events(
        organization_id=organization_id,
        kinds=[AgentEventKind.NEEDS_SECRET.value],
        assistant_thread=True,
    )


@pytest.mark.asyncio
class TestFindLeads:
    async def test_no_key_is_needs_setup_with_a_form_not_an_empty_list(self, org):
        with _no_platform_key():
            out = await outreach.find(org, {"titles": ["Clinic owner"]})
        assert out["status"] == "needs_setup"
        assert out["provider"] == "Apollo"
        assert "leads" not in out
        forms = await _key_forms(org)
        assert len(forms) == 1
        payload = forms[0].payload
        assert payload["provider_key"] == {
            "component": "data",
            "provider": "apollo",
            "label": "Apollo",
        }
        # The form asks for the key; it never carries one.
        assert all(not f.get("default") for f in payload["fields"])

    async def test_asking_twice_puts_one_form_on_the_thread(self, org):
        with _no_platform_key():
            await outreach.find(org, {"titles": ["Owner"]})
            await outreach.find(org, {"locations": ["Pune"]})
        assert len(await _key_forms(org)) == 1

    async def test_no_criteria_is_asked_for_not_searched(self, org):
        out = await outreach.find(org, {})
        assert out["status"] == "error"
        assert await _key_forms(org) == []

    async def test_the_workspaces_own_key_is_used_and_charged_as_its_own(self, org):
        async with db_client.async_session() as session:
            await organization_credentials.set_credential(
                session,
                organization_id=org,
                actor_user_id=None,
                component=CostComponent.DATA,
                provider="apollo",
                api_key="own-key-9876",
            )
            await session.commit()
        fake, seen = _apollo()
        charge = AsyncMock(return_value={"charged": True, "source": "own"})
        with fake, patch("api.services.billing.lookup_source.charge", charge):
            out = await outreach.find(org, {"titles": ["Owner"], "limit": 5})
        assert out["status"] == "success"
        assert [lead["name"] for lead in out["leads"]] == ["Asha Rao", "Vikram Shah"]
        assert all(lead["email_verified"] for lead in out["leads"])
        assert "1 more matched" in out["left_out"]
        assert seen[0].headers["x-api-key"] == "own-key-9876"
        source = charge.await_args.kwargs["source"]
        assert (source.kind, source.provider) == ("own", "apollo")
        assert charge.await_args.kwargs["verified"] == 2

    async def test_a_rejected_key_is_an_error_the_owner_can_act_on(self, org):
        async with db_client.async_session() as session:
            await organization_credentials.set_credential(
                session,
                organization_id=org,
                actor_user_id=None,
                component=CostComponent.DATA,
                provider="apollo",
                api_key="revoked-key",
            )
            await session.commit()
        fake, _ = _apollo(401)
        with fake:
            out = await outreach.find(org, {"titles": ["Owner"]})
        assert out["status"] == "error"
        assert "apollo" in out["reason"].lower()


def _no_platform_key():
    from api.services.configuration import platform_credentials

    return patch.object(
        platform_credentials, "resolve_api_key", AsyncMock(return_value=None)
    )


# --- the vault takes a lead-data key ---------------------------------------------


@pytest.mark.asyncio
class TestTheVault:
    async def test_a_workspace_can_hold_a_lead_data_key(self, org):
        async with db_client.async_session() as session:
            stored = await organization_credentials.set_credential(
                session,
                organization_id=org,
                actor_user_id=None,
                component="data",
                provider="apollo",
                api_key="abcd-1234",
            )
            await session.commit()
        assert stored.masked_key.endswith("1234")
        key = await leads.key_for(org, "apollo")
        assert (key.kind, key.value) == (leads.OWN, "abcd-1234")

    async def test_a_data_key_for_an_unknown_vendor_is_refused(self, org):
        async with db_client.async_session() as session:
            with pytest.raises(organization_credentials.OrganizationCredentialError):
                await organization_credentials.set_credential(
                    session,
                    organization_id=org,
                    actor_user_id=None,
                    component="data",
                    provider="treg",
                    api_key="x",
                )

    async def test_apollo_is_on_the_provider_keys_screens(self):
        from api.services.configuration.registry import known_providers

        assert known_providers()["apollo"] == ("data",)

    async def test_the_platform_key_is_second(self, org):
        from api.services.configuration import platform_credentials

        with patch.object(
            platform_credentials,
            "resolve_api_key",
            AsyncMock(return_value="platform-key"),
        ):
            key = await leads.key_for(org, "apollo")
        assert (key.kind, key.value) == (leads.PLATFORM, "platform-key")


# --- the key form stores into the vault -------------------------------------------


@pytest.mark.asyncio
class TestTheKeyForm:
    async def _form(self, org) -> int:
        with _no_platform_key():
            await outreach.find(org, {"titles": ["Owner"]})
        return int((await _key_forms(org))[0].id)

    async def test_the_key_goes_to_provider_keys_not_the_thread(self, org):
        from api.services.configuration import key_validation
        from api.services.workflow import secrets_request

        event_id = await self._form(org)
        with patch.object(
            key_validation,
            "validate_key",
            AsyncMock(
                return_value=key_validation.ValidationResult(
                    "valid", "Apollo accepted the key."
                )
            ),
        ):
            payload = await secrets_request.provide(
                organization_id=org,
                event_id=event_id,
                values={"api_key": "sk-live-apollo-5678"},
                user_id=1,
            )
        assert payload["provided"]["hint"] == "5678"
        assert "sk-live" not in json.dumps(payload)
        key = await leads.key_for(org, "apollo")
        assert (key.kind, key.value) == (leads.OWN, "sk-live-apollo-5678")
        rows = await db_client.agent_events(
            organization_id=org, assistant_thread=True, limit=20
        )
        assert all(
            "sk-live-apollo-5678" not in json.dumps(r.payload or {}) for r in rows
        )
        assert all("sk-live-apollo-5678" not in (r.summary or "") for r in rows)

    async def test_a_key_the_vendor_rejects_is_not_stored(self, org):
        from api.services.configuration import key_validation
        from api.services.workflow import secrets_request

        event_id = await self._form(org)
        with (
            patch.object(
                key_validation,
                "validate_key",
                AsyncMock(
                    return_value=key_validation.ValidationResult(
                        "invalid", "Apollo rejected that key."
                    )
                ),
            ),
            pytest.raises(secrets_request.SecretError, match="rejected"),
        ):
            await secrets_request.provide(
                organization_id=org,
                event_id=event_id,
                values={"api_key": "wrong"},
                user_id=1,
            )
        assert (await leads.key_for(org, "apollo")).kind == leads.NONE


# --- Decibyl holds them while the flag is on ----------------------------------------


class TestDecibylHoldsThem:
    def test_on_the_tools_and_rules_are_there(self, monkeypatch):
        from api.services.workflow import decibyl

        monkeypatch.setattr(constants, "OUTREACH_ENABLED", True)
        names = {t["name"] for t in decibyl.office_tools(None)}
        assert {"find_leads", "draft_outreach"} <= names
        prompt = decibyl.system_prompt(None)
        assert "find_leads" in prompt and "draft_outreach" in prompt
        assert AgentEventKind.NEEDS_SECRET.value in decibyl.thread_filter(None)["kinds"]

    def test_off_nothing_is_there(self, monkeypatch):
        from api.services.workflow import decibyl

        monkeypatch.setattr(constants, "OUTREACH_ENABLED", False)
        names = {t["name"] for t in decibyl.office_tools(None)}
        assert not {"find_leads", "draft_outreach"} & names
        assert "find_leads" not in decibyl.system_prompt(None)

    def test_a_search_keeps_the_tools_open_and_a_form_ends_the_round(self):
        from api.services.workflow import decibyl

        call = SimpleNamespace(name="find_leads")
        assert decibyl._was_a_read(call, {"status": "success"})
        assert not decibyl._was_a_read(call, {"status": "needs_setup"})
