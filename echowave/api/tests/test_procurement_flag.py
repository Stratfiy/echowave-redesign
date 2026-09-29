"""PROCUREMENT_DOCS_2026_09_ENABLED: off, nothing; on, everywhere it should be.

Off, no agent is offered a document tool, Decibyl's rules do not mention
them, its thread filter is unchanged and the routes are a 404. On, Decibyl
and every text agent get all of them, each named in the rules, and the thread
shows what they hand over. The routes serve only this workspace's files.
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from api import constants
from api.db.models import OrganizationModel
from api.enums import AgentEventKind
from api.services import features
from api.services.documents import register, tools
from api.services.workflow import decibyl


@pytest.fixture
def off(monkeypatch):
    monkeypatch.setattr(constants, "PROCUREMENT_DOCS_2026_09_ENABLED", False)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(constants, "PROCUREMENT_DOCS_2026_09_ENABLED", True)


def _names(schemas) -> set[str]:
    return {s["name"] for s in schemas}


class TestTheFlag:
    def test_it_is_registered_and_defaults_off(self):
        assert features.FLAGS["procurement_docs"] == "PROCUREMENT_DOCS_2026_09_ENABLED"
        # api/.env.test does not set it, so this is the default.
        assert constants.PROCUREMENT_DOCS_2026_09_ENABLED is False


class TestDecibyl:
    def test_off_it_has_none_of_them_and_nothing_else_changed(self, off):
        assert not (_names(decibyl.office_tools()) & set(tools.NAMES))
        assert decibyl.system_prompt() == decibyl.SYSTEM
        assert AgentEventKind.DELIVERABLE.value not in decibyl.thread_filter()["kinds"]

    def test_on_it_has_all_of_them_each_with_a_rule(self, on):
        assert _names(decibyl.office_tools()) >= set(tools.NAMES)
        prompt = decibyl.system_prompt()
        missing = [t["name"] for t in decibyl.office_tools() if t["name"] not in prompt]
        assert not missing, f"tools with no rule: {missing}"
        assert AgentEventKind.DELIVERABLE.value in decibyl.thread_filter()["kinds"]

    @pytest.mark.asyncio
    async def test_on_a_call_reaches_the_tool_with_the_threads_organization(self, on):
        call = SimpleNamespace(
            id="c1", name=tools.LIST, arguments={"organization_id": 999}
        )
        with patch(
            "api.services.documents.tools.run",
            return_value={"status": "success", "entries": []},
        ) as run:
            result = await decibyl._tool(42, call)
        assert result["status"] == "success"
        assert run.call_args.kwargs["organization_id"] == 42
        assert run.call_args.kwargs["ref_id"] == "decibyl:42:c1"

    @pytest.mark.asyncio
    async def test_off_a_call_reaches_nothing(self, off):
        call = SimpleNamespace(id="c1", name=tools.DRAFT, arguments={})
        result = await decibyl._tool(42, call)
        assert result["status"] == "unavailable"

    def test_a_missing_answer_keeps_the_models_tools(self, on):
        draft = SimpleNamespace(name=tools.DRAFT)
        assert decibyl._was_a_read(draft, {"status": "missing"}) is True
        assert decibyl._was_a_read(draft, {"status": "drafted"}) is False
        assert decibyl._was_a_read(
            SimpleNamespace(name=tools.READ), {"status": "success"}
        )


@pytest.mark.asyncio
class TestAgents:
    async def _functions(self, *, voice: bool):
        from api.services.workflow.pipecat_engine_context_composer import (
            compose_functions_for_node,
        )

        node = SimpleNamespace(
            document_uuids=[],
            tool_uuids=[],
            out_edges=[],
            extraction_variables=[],
            extraction_enabled=False,
            data=SimpleNamespace(),
        )
        with patch(
            "api.services.workflow.pipecat_engine_context_composer.argument_properties",
            return_value={},
        ):
            functions = await compose_functions_for_node(
                node=node,
                custom_tool_manager=None,
                can_ask_for_decision=not voice,
            )
        return {getattr(f, "name", None) or f.get("name") for f in functions}

    async def test_off_no_agent_is_offered_them(self, off):
        assert not (await self._functions(voice=False) & set(tools.NAMES))

    async def test_on_a_text_agent_is_offered_all_of_them(self, on):
        assert await self._functions(voice=False) >= set(tools.NAMES)

    async def test_on_a_voice_agent_is_not(self, on):
        assert not (await self._functions(voice=True) & set(tools.NAMES))


async def _org(session, name: str) -> int:
    org = OrganizationModel(
        provider_id=f"proc-route-{name}-{datetime.now(UTC).timestamp()}",
        quota_decibyl_tokens=0,
    )
    session.add(org)
    await session.flush()
    return org.id


@pytest.mark.asyncio
class TestRoutes:
    async def test_off_the_routes_are_a_404(self, off, test_client_factory, db_session):
        user, _ = await db_session.get_or_create_user_by_provider_id("proc-off-user")
        async with test_client_factory(user) as client:
            listed = await client.get("/api/v1/procurement/documents")
            download = await client.get("/api/v1/procurement/documents/1/files/pdf")
        assert listed.status_code == 404
        assert download.status_code == 404

    async def test_on_a_workspace_downloads_its_own_file_and_not_anothers(
        self, on, test_client_factory, db_session, async_session, monkeypatch
    ):
        from api.services import storage

        class Signer:
            async def aget_signed_url(self, key, expiration=3600, **_):
                return f"https://files.test/{key}"

        monkeypatch.setattr(storage, "storage_fs", Signer())

        mine = await _org(async_session, "mine")
        theirs = await _org(async_session, "theirs")
        row = await register.create(
            async_session,
            organization_id=mine,
            kind="purchase_order",
            issue_date=date(2026, 9, 26),
            status="awaiting_approval",
            counterparty_name="Bharat",
        )
        row.pdf_key = register.storage_key(mine, row.id, "PO-26-27-0001.pdf")
        await async_session.flush()

        me, _ = await db_session.get_or_create_user_by_provider_id(
            f"proc-me-{datetime.now(UTC).timestamp()}"
        )
        me.selected_organization_id = mine
        other, _ = await db_session.get_or_create_user_by_provider_id(
            f"proc-other-{datetime.now(UTC).timestamp()}"
        )
        other.selected_organization_id = theirs

        async with test_client_factory(me) as client:
            redirect = await client.get(
                f"/api/v1/procurement/documents/{row.id}/files/pdf",
                follow_redirects=False,
            )
            link = await client.get(
                f"/api/v1/procurement/documents/{row.id}/files/pdf?redirect=false"
            )
            missing = await client.get(
                f"/api/v1/procurement/documents/{row.id}/files/docx"
            )
            listed = await client.get("/api/v1/procurement/documents")
        assert redirect.status_code == 307
        assert redirect.headers["location"].startswith(
            f"https://files.test/procurement/{mine}/"
        )
        assert link.json()["filename"] == "PO-26-27-0001.pdf"
        assert missing.status_code == 404
        assert [e["number"] for e in listed.json()["entries"]] == [row.number]

        async with test_client_factory(other) as client:
            stolen = await client.get(
                f"/api/v1/procurement/documents/{row.id}/files/pdf"
            )
            listed = await client.get("/api/v1/procurement/documents")
        assert stolen.status_code == 404
        assert listed.json()["entries"] == []
