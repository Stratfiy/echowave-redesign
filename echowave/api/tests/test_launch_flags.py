"""FLAG-1 (KAN-275): the launch flags are registered, off by default, and
can be turned on for one organisation before everyone."""

import pytest
from fastapi import HTTPException

from api import constants
from api.services import features

LAUNCH_FLAGS = [
    "invite_only_signup",
    "trial_plan",
    "byok_text",
    "marketplace_publishing",
    "whatsapp_channel_ui",
    "voice_number_flow",
    "approval_scopes",
    "projects",
    "agent_faces",
    "ui_shell_v2",
    "decibyl_channels",
    "decibyl_telegram",
    "decibyl_slack",
    "decibyl_teams",
]

PRE_EXISTING_SWITCHES = [
    "budget_policies",
    "plan_ladder",
    "decibyl_tools",
    "agent_builder",
    "managed_telephony",
]


@pytest.mark.parametrize("name", LAUNCH_FLAGS + PRE_EXISTING_SWITCHES)
def test_every_launch_flag_is_registered_and_names_a_constant(name):
    assert name in features.FLAGS
    assert hasattr(constants, features.FLAGS[name])


@pytest.mark.parametrize("name", LAUNCH_FLAGS)
def test_launch_flags_are_off_by_default(name, monkeypatch):
    monkeypatch.setattr(constants, "FEATURE_ORG_OVERRIDES", "")
    assert features.is_on(name) is False
    assert features.public()[name] is False


def test_override_parsing_table(monkeypatch):
    cases = [
        ("", {}),
        ("trial_plan:7", {"trial_plan": {7}}),
        ("trial_plan:7,9;projects:7", {"trial_plan": {7, 9}, "projects": {7}}),
        # whitespace, a trailing separator and a blank entry are tolerated
        (" trial_plan : 7 , 9 ; ; ", {"trial_plan": {7, 9}}),
        # an unknown feature or a non-numeric id is ignored, never raised
        ("no_such_flag:1;trial_plan:x,3", {"trial_plan": {3}}),
        ("trial_plan", {}),
    ]
    for raw, expected in cases:
        monkeypatch.setattr(constants, "FEATURE_ORG_OVERRIDES", raw)
        got = {k: set(v) for k, v in features.org_overrides().items()}
        assert got == expected, raw


def test_is_on_global_org_and_neither(monkeypatch):
    monkeypatch.setattr(constants, "TRIAL_PLAN_ENABLED", False)
    monkeypatch.setattr(constants, "FEATURE_ORG_OVERRIDES", "trial_plan:42")

    assert features.is_on("trial_plan") is False
    assert features.is_on("trial_plan", organization_id=42) is True
    assert features.is_on("trial_plan", organization_id=43) is False
    assert features.is_on("trial_plan", organization_id=None) is False

    monkeypatch.setattr(constants, "TRIAL_PLAN_ENABLED", True)
    assert features.is_on("trial_plan", organization_id=43) is True


def test_for_organization_merges_overrides_over_the_global_map(monkeypatch):
    monkeypatch.setattr(constants, "TRIAL_PLAN_ENABLED", False)
    monkeypatch.setattr(constants, "PROJECTS_ENABLED", True)
    monkeypatch.setattr(constants, "FEATURE_ORG_OVERRIDES", "trial_plan:42")

    listed = features.for_organization(42)
    other = features.for_organization(43)
    assert set(listed) == set(features.FLAGS)
    assert listed["trial_plan"] is True and other["trial_plan"] is False
    assert listed["projects"] is True and other["projects"] is True
    # /health stays global: an override never leaks into the public map
    assert features.public()["trial_plan"] is False


def test_require_per_organization_honours_the_override(monkeypatch):
    monkeypatch.setattr(constants, "PROJECTS_ENABLED", False)
    monkeypatch.setattr(constants, "FEATURE_ORG_OVERRIDES", "projects:42")

    class User:
        selected_organization_id = 42

    class Other:
        selected_organization_id = 43

    check = features.require("projects", per_organization=True)
    assert check.feature == "projects"
    check(user=User())
    with pytest.raises(HTTPException) as caught:
        check(user=Other())
    assert caught.value.status_code == 404

    # the default dependency stays global and session-free
    plain = features.require("projects")
    with pytest.raises(HTTPException):
        plain()
