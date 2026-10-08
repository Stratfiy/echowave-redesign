"""Auto: each piece of work runs on the model for its kind.

Rules always decide when the decision model is off, unsure, slow or down;
in shadow mode Laya is asked and logged but never acts.
"""

import httpx
import pytest

from api import constants
from api.schemas.ai_model_configuration import (
    DecibylManagedAIModelConfiguration,
    OrganizationAIModelConfigurationV2,
)
from api.services.routing import brain, decision

_REAL_CLIENT = httpx.AsyncClient


@pytest.mark.parametrize(
    "text,attachments,kind",
    [
        ("hi", 0, "quick"),
        ("What time do you open?", 0, "quick"),
        ("Draft a reply to Ravi about the proposal", 0, "steps"),
        ("Remind me to call the bank tomorrow", 0, "steps"),
        ("here", 1, "steps"),
        (
            "Compare our three suppliers on price, delivery and quality and tell me which to keep",
            0,
            "deep",
        ),
        ("x" * 1300, 0, "deep"),
    ],
)
def test_the_rules_sort_work_by_kind(text, attachments, kind):
    assert brain.by_rules(text, attachments=attachments) == kind


def _laya(monkeypatch, *, answer=None, raises=None, mode="on"):
    monkeypatch.setattr(constants, "LAYA_URL", "http://laya.test")
    monkeypatch.setattr(constants, "LAYA_ROUTING", mode)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read()
        if raises:
            raise raises
        return httpx.Response(200, json={"answers": {"decision": answer}})

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _REAL_CLIENT(*args, **kwargs)

    monkeypatch.setattr(decision.httpx, "AsyncClient", client)
    return seen


@pytest.mark.asyncio
async def test_laya_decides_when_on(monkeypatch):
    seen = _laya(monkeypatch, answer={"choice": "deep", "confidence": 0.91})
    route = await brain.route("ek report banao, teen options compare karke")
    assert (route.kind, route.preset, route.source) == ("deep", "deep", "laya")
    assert b"criteria" in seen["body"] and b"deep" in seen["body"]


@pytest.mark.asyncio
async def test_unsure_laya_hands_back_to_the_rules(monkeypatch):
    _laya(monkeypatch, answer={"choice": "deep", "confidence": 0.3})
    route = await brain.route("hi")
    assert (route.kind, route.source, route.abstained) == (
        "quick",
        "laya_fallback",
        "low_confidence",
    )


@pytest.mark.asyncio
async def test_an_answer_outside_the_labels_is_not_taken(monkeypatch):
    _laya(monkeypatch, answer={"choice": "send_money", "confidence": 0.99})
    route = await brain.route("hi")
    assert (route.kind, route.abstained) == ("quick", "malformed")


@pytest.mark.asyncio
async def test_a_down_or_slow_laya_never_breaks_a_reply(monkeypatch):
    _laya(monkeypatch, raises=httpx.ConnectError("refused"))
    assert (await brain.route("hi")).abstained == "error"
    _laya(monkeypatch, raises=httpx.ReadTimeout("slow"))
    assert (await brain.route("hi")).abstained == "timeout"


@pytest.mark.asyncio
async def test_shadow_asks_laya_but_the_rules_act(monkeypatch):
    _laya(monkeypatch, answer={"choice": "deep", "confidence": 0.95}, mode="shadow")
    route = await brain.route("hi")
    assert (route.kind, route.source, route.laya_kind) == ("quick", "rules", "deep")


@pytest.mark.asyncio
async def test_without_laya_the_rules_decide(monkeypatch):
    monkeypatch.setattr(constants, "LAYA_URL", "")
    route = await brain.route("Draft a reply to Ravi")
    assert (route.kind, route.source) == ("steps", "rules")


@pytest.mark.asyncio
async def test_auto_only_routes_a_workspace_on_auto(monkeypatch):
    monkeypatch.setattr(constants, "LAYA_URL", "")
    from api.services.configuration import ai_model_configuration as amc

    stored = {"config": None}

    async def get(_org):
        return stored["config"]

    monkeypatch.setattr(amc, "get_organization_ai_model_configuration_v2", get)

    # Nothing stored: a new workspace is on Auto.
    assert await brain.auto_route(1, "hi") is not None
    # Pinned to an exact model: Auto is not in charge.
    stored["config"] = OrganizationAIModelConfigurationV2(
        mode="decibyl",
        decibyl=DecibylManagedAIModelConfiguration(
            slots={"llm": "anthropic/claude-opus-5-5"}
        ),
    )
    assert await brain.auto_route(1, "hi") is None
    # Picked Everyday on purpose: not Auto either.
    stored["config"] = OrganizationAIModelConfigurationV2(
        mode="decibyl", decibyl=DecibylManagedAIModelConfiguration(llm_tier="default")
    )
    assert await brain.auto_route(1, "hi") is None


@pytest.mark.asyncio
async def test_an_agent_with_its_own_brain_is_not_routed(monkeypatch):
    monkeypatch.setattr(constants, "LAYA_URL", "")
    from api.services.configuration.agent_options import managed_stack_override

    own = managed_stack_override(voice="", llm_tier="accurate")
    assert brain.agent_follows_workspace(own) is False
    assert await brain.auto_route(1, "hi", workflow_configurations=own) is None
    plain = managed_stack_override(voice="", llm_tier="default")
    assert brain.agent_follows_workspace(plain) is True
    assert brain.agent_follows_workspace({}) is True


def test_auto_is_the_default_and_resolves_to_everyday_where_nothing_routes():
    from api.services.configuration import managed_tiers

    assert DecibylManagedAIModelConfiguration().llm_tier == "auto"
    assert managed_tiers.LLM_TIERS[0] == "auto"
    assert managed_tiers.resolve("llm", "auto") == managed_tiers.resolve(
        "llm", "default"
    )
