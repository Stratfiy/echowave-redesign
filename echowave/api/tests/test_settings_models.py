"""Model defaults showing inheritance (screen 26; handoff 8, 25, 30).

Done when: each slot says where its value comes from (platform default or
this workspace), whether it can actually run here (never "ready" without the
key), its configured fallback and each agent's inheritance; a save from a
stale screen is a conflict that carries what runs now; quick A -> B saves
leave the server on B; only an admin changes the workspace's defaults; off,
the route is not there and the old Models screen is unchanged.
"""

from __future__ import annotations

import pytest

from api import constants
from api.services.configuration import managed_resolution
from api.services.settings import models as model_defaults
from api.tests.support.settings_people import clean, client_as, make_people

PLATFORM = {
    "llm": ["anthropic", "openai", "sarvam"],
    "stt": ["sarvam"],
    "tts": ["sarvam"],
}


@pytest.fixture
async def people(test_engine):
    found = await make_people("setmod")
    try:
        yield found
    finally:
        await clean(found)


@pytest.fixture
def inheritance_on(monkeypatch):
    monkeypatch.setattr(constants, "MODEL_INHERITANCE_ENABLED", True)


@pytest.fixture
def platform(monkeypatch):
    async def catalog(_session):
        return PLATFORM

    monkeypatch.setattr(managed_resolution, "platform_provider_catalog", catalog)


def _slot(view, key):
    return next(s for s in view["slots"] if s["key"] == key)


@pytest.mark.asyncio
class TestInheritance:
    async def test_off_the_route_is_not_there(self, people):
        async with client_as(people.as_user(people.a)) as c:
            assert (
                await c.get("/api/v1/settings/models/inheritance")
            ).status_code == 404

    async def test_a_new_workspace_inherits_the_platform(
        self, people, inheritance_on, platform
    ):
        async with client_as(people.as_user(people.b)) as c:
            view = (await c.get("/api/v1/settings/models/inheritance")).json()
        brain = _slot(view, "llm")
        assert brain["source"] == "platform"
        assert brain["readiness"] == "ready" and brain["revision"]
        assert view["precedence"][0].startswith("Decibyl's allowed models")
        voice = _slot(view, "tts")
        assert set(voice["sarvam"]) == {"in_use", "offered"}

    async def test_ready_only_when_the_key_is_held(
        self, people, inheritance_on, monkeypatch
    ):
        async def nothing(_session):
            return {}

        monkeypatch.setattr(managed_resolution, "platform_provider_catalog", nothing)
        view = await model_defaults.view(people.org)
        brain = _slot(view, "llm")
        assert brain["readiness"] == "needs_setup"
        assert "not set up on this deployment" in brain["readiness_reason"]

    async def test_a_choice_is_the_workspaces_and_stale_saves_conflict(
        self, people, inheritance_on, platform
    ):
        async with client_as(people.as_user(people.a)) as c:
            view = (await c.get("/api/v1/settings/models/inheritance")).json()
            brain = _slot(view, "llm")
            other = next(o for o in brain["ours"] if o["value"] != brain["current"])
            saved = await c.put(
                "/api/v1/settings/models/inheritance",
                json={
                    "slot": "llm",
                    "value": other["value"],
                    "revision": brain["revision"],
                },
            )
            assert saved.status_code == 200, saved.text
            after = _slot(saved.json(), "llm")
            assert after["current"] == other["value"] and after["source"] == "workspace"
            # A second tab still holding the first revision.
            stale = await c.put(
                "/api/v1/settings/models/inheritance",
                json={
                    "slot": "llm",
                    "value": brain["current"],
                    "revision": brain["revision"],
                },
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["stored"]["current"] == other["value"]

    async def test_quick_a_then_b_ends_on_b(self, people, inheritance_on, platform):
        view = await model_defaults.view(people.org)
        brain = _slot(view, "llm")
        a, b = [o["value"] for o in brain["ours"]][:2]
        first = await model_defaults.choose(
            people.org, slot="llm", value=a, revision=None
        )
        second = await model_defaults.choose(
            people.org, slot="llm", value=b, revision=_slot(first, "llm")["revision"]
        )
        assert _slot(second, "llm")["current"] == b
        assert _slot(await model_defaults.view(people.org), "llm")["current"] == b

    async def test_only_an_admin_changes_the_workspace(
        self, people, inheritance_on, platform
    ):
        async with client_as(people.as_user(people.b)) as c:
            answer = await c.put(
                "/api/v1/settings/models/inheritance",
                json={"slot": "llm", "value": "tier:auto"},
            )
        assert answer.status_code == 403

    async def test_every_agent_says_whether_it_inherits(
        self, people, inheritance_on, platform
    ):
        from api.db import db_client

        workflow = await db_client.create_workflow(
            "Front desk", {"nodes": [], "edges": []}, people.a.id, people.org
        )
        view = await model_defaults.view(people.org)
        agent = next(a for a in view["agents"] if a["workflow_id"] == workflow.id)
        assert agent["slots"]["llm"] == {
            "inherits": True,
            "label": "Inherits workspace",
        }
