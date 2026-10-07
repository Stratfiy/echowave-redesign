"""Launch stream care: every part is behind its own switch, off by default,
and turning it off restores today's behaviour.

Done when: the five flags are registered (constants, the registry with a
description, the UI's list); every care route is a 404 while its flag is
off and present once on; Decibyl is handed no care tool and no care rule
while off, and all four once on; and Simple mode is neither offered nor
saved while ``care_simple_mode`` is off.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api import constants
from api.services import care, features
from api.services.workflow import decibyl
from api.tests import care_support as cs

ROUTES = [
    ("get", "/api/v1/care/circle", None, "CARE_FAMILY_CIRCLE_ENABLED"),
    ("get", "/api/v1/care/family", None, "CARE_FAMILY_CIRCLE_ENABLED"),
    (
        "post",
        "/api/v1/care/family/accept",
        {"code": "ABCD-EFGH"},
        "CARE_FAMILY_CIRCLE_ENABLED",
    ),
    ("get", "/api/v1/care/medicines", None, "CARE_MEDICINE_CALLS_ENABLED"),
    ("post", "/api/v1/care/scam-check", {"text": "hello"}, "CARE_SCAM_CHECK_ENABLED"),
    ("get", "/api/v1/care/scam-check/recent", None, "CARE_SCAM_CHECK_ENABLED"),
    ("get", "/api/v1/care/help/topics", None, "CARE_TECH_HELP_ENABLED"),
    (
        "post",
        "/api/v1/care/help/sessions",
        {"question": "bigger text"},
        "CARE_TECH_HELP_ENABLED",
    ),
]


def test_flags_are_registered_described_and_off():
    for flag in care.FLAGS:
        assert flag in features.FLAGS
        assert features.DESCRIPTIONS.get(flag)
        assert getattr(constants, features.FLAGS[flag]) is False
        assert features.is_on(flag) is False
    ui = (Path(__file__).parents[2] / "ui/src/lib/features.ts").read_text()
    for flag in care.FLAGS:
        assert f'"{flag}"' in ui


@pytest.fixture
async def someone(test_engine):
    user = await cs.person("flags")
    org = await cs.workspace(user.id)
    yield user, org
    await cs.cleanup(org)


@pytest.mark.asyncio
async def test_every_route_is_absent_while_off(someone):
    user, org = someone
    async with cs.client(user.id, org) as c:
        assert (await c.get("/api/v1/care/status")).status_code == 404
        for method, path, body, _ in ROUTES:
            r = await getattr(c, method)(path, **({"json": body} if body else {}))
            assert r.status_code == 404, path


@pytest.mark.asyncio
async def test_each_route_arrives_with_its_own_flag(someone, monkeypatch):
    user, org = someone
    for method, path, body, flag in ROUTES:
        with monkeypatch.context() as m:
            m.setattr(constants, flag, True)
            async with cs.client(user.id, org) as c:
                r = await getattr(c, method)(path, **({"json": body} if body else {}))
                assert r.status_code != 404 or path.endswith("/accept"), path
                status = await c.get("/api/v1/care/status")
                assert status.status_code == 200
                states = {p["key"]: p["state"] for p in status.json()["parts"]}
                on_key = next(k for k, v in features.FLAGS.items() if v == flag)
                assert states[on_key] != "disabled"
                assert sum(1 for s in states.values() if s == "disabled") == 4


def test_decibyl_has_no_care_tool_or_rule_while_off():
    names = {t["name"] for t in decibyl.office_tools(1)}
    assert not names & {"check_for_scam", "phone_help_start", "set_medicine_reminder"}
    assert decibyl.system_prompt(1) == decibyl.system_prompt(None)
    assert "check_for_scam" not in decibyl.system_prompt(1)


def test_decibyl_is_handed_each_tool_and_told_its_rule_once_on(monkeypatch):
    cs.all_on(monkeypatch)
    names = {t["name"] for t in decibyl.office_tools(1)}
    wanted = {
        "check_for_scam",
        "phone_help_start",
        "phone_help_answer",
        "set_medicine_reminder",
    }
    assert wanted <= names
    prompt = decibyl.system_prompt(1)
    for name in wanted:
        assert name in prompt
    assert "never suggest a dose" in prompt.lower()


@pytest.mark.asyncio
async def test_simple_mode_is_not_offered_or_saved_while_off(someone, monkeypatch):
    user, org = someone
    monkeypatch.setattr(constants, "MEMBER_PREFERENCES_ENABLED", True)
    async with cs.client(user.id, org) as c:
        read = (await c.get("/api/v1/me/preferences")).json()
        assert read["simple_mode"] is None
        refused = await c.put(
            "/api/v1/me/preferences", json={"simple_mode": True, "revision": 0}
        )
        assert refused.status_code == 422
        # Everything else saves exactly as before.
        saved = await c.put(
            "/api/v1/me/preferences", json={"language": "ta-IN", "revision": 0}
        )
        assert saved.status_code == 200 and saved.json()["simple_mode"] is None
