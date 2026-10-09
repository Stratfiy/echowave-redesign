"""A voice changed in the agent's About panel takes effect, and nothing else does.

Seen in an audit of main in October 2026: the About panel saved the voice
into the agent's draft, and nothing on that page publishes. So the change
waited silently and went live later, by surprise, bundled with whatever edit
somebody next published from a card in the thread.

Now the voice row puts its change live as a version of its own. These are
DB integration tests: a real agent, real versions, a real audit table.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.db.models import AuditEntryModel, OrganizationModel, UserModel
from api.services.configuration import model_slot
from api.services.configuration.ai_model_configuration import (
    WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY as OVERRIDE,
)
from api.services.workflow import audit_log, live_voice

GRAPH = {
    "nodes": [
        {
            "id": "1",
            "type": "startCall",
            "position": {"x": 0, "y": 0},
            "data": {"name": "Start", "prompt": "Help the caller."},
        }
    ],
    "edges": [],
}

STACK = {
    "architecture": "pipeline",
    "stt": {"provider": "decibyl", "model": "default", "api_key": ""},
    "llm": {"provider": "decibyl", "model": "accurate", "api_key": ""},
    "tts": {
        "provider": "sarvam",
        "model": "bulbul:v2",
        "voice": "anushka",
        "api_key": "",
        "use_platform_key": True,
    },
}
CONFIG = {OVERRIDE: {"version": 3, "stack": STACK}, "max_call_duration": 300}


def _graph(prompt: str) -> dict:
    graph = copy.deepcopy(GRAPH)
    graph["nodes"][0]["data"]["prompt"] = prompt
    return graph


def _tts(configurations: dict) -> dict:
    return configurations[OVERRIDE]["stack"]["tts"]


@pytest.fixture
async def agent(db_session, async_session):
    org = OrganizationModel(provider_id="test-org-live-voice")
    async_session.add(org)
    await async_session.flush()
    user = UserModel(
        provider_id="test-user-live-voice", selected_organization_id=org.id
    )
    async_session.add(user)
    await async_session.flush()
    workflow = await db_session.create_workflow(
        name="Front desk",
        workflow_definition=GRAPH,
        user_id=user.id,
        organization_id=org.id,
        workflow_configurations=copy.deepcopy(CONFIG),
    )
    return SimpleNamespace(org=org, user=user, workflow=workflow)


def _sellable():
    """The test database carries no catalogue; the gate itself is tested below."""
    return patch.object(model_slot, "ensure_sellable", AsyncMock())


async def _apply(agent, **overrides):
    arguments = dict(
        workflow_id=agent.workflow.id,
        organization_id=agent.org.id,
        user_id=agent.user.id,
        component="tts",
        provider="sarvam",
        model="bulbul:v2",
        voice="manisha",
    )
    arguments.update(overrides)
    with _sellable():
        return await live_voice.apply_voice_now(**arguments)


@pytest.mark.asyncio
class TestTheVoiceGoesLive:
    async def test_with_no_draft_the_voice_is_live_at_once(self, db_session, agent):
        applied = await _apply(agent)
        assert applied.version_number == 2 and applied.draft_kept is False

        live = await db_session.get_published_definition(
            agent.workflow.id, agent.org.id
        )
        assert live.version_number == 2
        assert _tts(live.workflow_configurations)["voice"] == "manisha"
        # Only the voice moved.
        assert live.workflow_configurations["max_call_duration"] == 300
        assert live.workflow_json == GRAPH
        assert await db_session.get_draft_version(agent.workflow.id) is None

        refreshed = await db_session.get_workflow_by_id(agent.workflow.id)
        assert refreshed.released_definition_id == live.id
        assert _tts(refreshed.workflow_configurations)["voice"] == "manisha"
        statuses = sorted(
            (v.version_number, v.status)
            for v in await db_session.get_workflow_versions(agent.workflow.id)
        )
        assert statuses == [(1, "archived"), (2, "published")]

    async def test_a_waiting_draft_is_not_published_with_it(self, db_session, agent):
        await db_session.save_workflow_draft(
            agent.workflow.id, workflow_definition=_graph("Unfinished rewrite.")
        )
        applied = await _apply(agent)
        assert applied.draft_kept is True

        live = await db_session.get_published_definition(
            agent.workflow.id, agent.org.id
        )
        assert _tts(live.workflow_configurations)["voice"] == "manisha"
        # The draft's prompt did not ride along.
        assert live.workflow_json["nodes"][0]["data"]["prompt"] == "Help the caller."

        draft = await db_session.get_draft_version(agent.workflow.id)
        assert (
            draft.workflow_json["nodes"][0]["data"]["prompt"] == "Unfinished rewrite."
        )
        # ...but carries the voice, so publishing it later keeps it.
        assert _tts(draft.workflow_configurations)["voice"] == "manisha"
        assert draft.version_number > live.version_number

        await db_session.publish_workflow_draft(agent.workflow.id)
        later = await db_session.get_published_definition(
            agent.workflow.id, agent.org.id
        )
        assert _tts(later.workflow_configurations)["voice"] == "manisha"
        assert (
            later.workflow_json["nodes"][0]["data"]["prompt"] == "Unfinished rewrite."
        )

    async def test_the_voice_settings_go_live_too(self, db_session, agent):
        lexicon = [{"find": "Dr.", "say": "Doctor"}]
        await _apply(agent, settings={"pronunciation_lexicon": lexicon})
        live = await db_session.get_published_definition(
            agent.workflow.id, agent.org.id
        )
        assert live.workflow_configurations["pronunciation_lexicon"] == lexicon

    async def test_it_is_audited(self, async_session, agent):
        await _apply(agent)
        rows = (
            (
                await async_session.execute(
                    select(AuditEntryModel).where(
                        AuditEntryModel.organization_id == agent.org.id,
                        AuditEntryModel.action == audit_log.AGENT_PUBLISHED,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].after["via"] == "about_voice"
        assert rows[0].after["tts"]["voice"] == "manisha"
        assert rows[0].before["tts"]["voice"] == "anushka"


@pytest.mark.asyncio
class TestWhatIsRefused:
    async def test_a_setting_the_voice_panel_does_not_own(self, db_session, agent):
        with pytest.raises(live_voice.SettingsRefused, match="max_call_duration"):
            await _apply(agent, settings={"max_call_duration": 5})
        live = await db_session.get_published_definition(
            agent.workflow.id, agent.org.id
        )
        assert live.version_number == 1

    async def test_another_accounts_agent(self, db_session, async_session, agent):
        other = OrganizationModel(provider_id="test-org-live-voice-other")
        async_session.add(other)
        await async_session.flush()
        with pytest.raises(live_voice.AgentNotFound):
            await _apply(agent, organization_id=other.id)
        live = await db_session.get_published_definition(
            agent.workflow.id, agent.org.id
        )
        assert live.version_number == 1

    async def test_a_model_not_on_the_catalogue(self, db_session, agent):
        arguments = dict(
            workflow_id=agent.workflow.id,
            organization_id=agent.org.id,
            user_id=agent.user.id,
            component="tts",
            provider="nobody",
            model="nothing",
            voice=None,
        )
        with pytest.raises(model_slot.SlotRefused, match="not on offer"):
            await live_voice.apply_voice_now(**arguments)
        live = await db_session.get_published_definition(
            agent.workflow.id, agent.org.id
        )
        assert live.version_number == 1


@pytest.mark.asyncio
class TestTheRoute:
    async def _put(self, agent, body):
        from api.app import app
        from api.services.auth.depends import get_user

        app.dependency_overrides[get_user] = lambda: agent.user
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                return await client.put(
                    f"/api/v1/workflow/{agent.workflow.id}/voice/live", json=body
                )
        finally:
            app.dependency_overrides.pop(get_user, None)

    async def test_puts_the_voice_live(self, db_session, agent):
        with _sellable():
            response = await self._put(
                agent,
                {
                    "component": "tts",
                    "provider": "sarvam",
                    "model": "bulbul:v2",
                    "voice": "manisha",
                    "speed": 1.1,
                },
            )
        assert response.status_code == 200, response.text
        assert response.json()["version_number"] == 2
        assert response.json()["draft_kept"] is False
        live = await db_session.get_published_definition(
            agent.workflow.id, agent.org.id
        )
        assert _tts(live.workflow_configurations)["voice"] == "manisha"
        assert _tts(live.workflow_configurations)["speed"] == 1.1

    async def test_only_the_voice_slot(self, agent):
        response = await self._put(
            agent, {"component": "llm", "provider": "decibyl", "model": "accurate"}
        )
        assert response.status_code == 422

    async def test_a_foreign_setting_is_a_422(self, agent):
        with _sellable():
            response = await self._put(
                agent,
                {
                    "component": "tts",
                    "provider": "sarvam",
                    "model": "bulbul:v2",
                    "settings": {"max_call_duration": 5},
                },
            )
        assert response.status_code == 422
        assert "max_call_duration" in response.json()["detail"]
