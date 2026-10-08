"""The browser's model call survives a model that refuses a forced tool.

browser-use forces ``tool_choice`` on every step. The default browser model
refuses that with a 400, so on staging every browse ended "The model could
not answer just now." The bridge now retries once with ``auto``.
"""

from __future__ import annotations

import pytest

from api.services.browser import bridge

pytestmark = pytest.mark.asyncio

REFUSAL = (
    '{"type":"error","error":{"type":"invalid_request_error","message":'
    '"tool_choice: type \\"tool\\" and \\"any\\" are not supported for this model."}}'
)
OK = {"content": [{"type": "tool_use", "name": "act", "input": {}}], "usage": {}}


def _body(choice):
    return {
        "messages": [{"role": "user", "content": "open the page"}],
        "tools": [{"name": "act", "input_schema": {"type": "object"}}],
        "tool_choice": choice,
    }


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    async def key():
        return "k"

    async def record(**_kw):
        return None

    monkeypatch.setattr(bridge, "_key", key)
    monkeypatch.setattr(bridge.model_usage, "record", record)
    return calls


def _vendor(monkeypatch, calls, refuse_forced: bool):
    async def post(payload, key):
        calls.append(payload)
        forced = (payload.get("tool_choice") or {}).get("type") in ("any", "tool")
        if refuse_forced and forced:
            raise bridge._ForcedToolChoiceRefused()
        return OK

    monkeypatch.setattr(bridge, "_post", post)


async def test_a_refused_forced_choice_is_retried_as_auto(monkeypatch, sent):
    _vendor(monkeypatch, sent, refuse_forced=True)
    reply, _cost = await bridge.call(
        _body({"type": "any"}), organization_id=1, signals=[]
    )
    assert reply == OK
    assert [c["tool_choice"]["type"] for c in sent] == ["any", "auto"]
    assert sent[1]["tools"] == sent[0]["tools"]


async def test_a_model_that_accepts_it_is_asked_once(monkeypatch, sent):
    _vendor(monkeypatch, sent, refuse_forced=False)
    await bridge.call(
        _body({"type": "tool", "name": "act"}), organization_id=1, signals=[]
    )
    assert [c["tool_choice"]["type"] for c in sent] == ["tool"]


async def test_an_unforced_request_is_not_rewritten(monkeypatch, sent):
    async def post(payload, key):
        sent.append(payload)
        raise bridge._ForcedToolChoiceRefused()

    monkeypatch.setattr(bridge, "_post", post)
    with pytest.raises(bridge.BridgeError):
        await bridge.call(_body({"type": "auto"}), organization_id=1, signals=[])
    assert len(sent) == 1


def test_the_refusal_is_recognised_and_other_400s_are_not():
    assert bridge._refuses_forced_tool_choice(400, REFUSAL)
    assert not bridge._refuses_forced_tool_choice(
        400, '{"error":"max_tokens too large"}'
    )
    assert not bridge._refuses_forced_tool_choice(500, REFUSAL)
