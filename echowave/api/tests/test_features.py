"""The one place a feature flag is read, and the one place a refusal becomes a
400 -- so no route or screen needs its own copy of either."""

import json

import pytest
from fastapi import HTTPException

from api import constants
from api.services import features
from api.services.refused import Refused


def test_every_flag_names_a_setting_that_exists():
    for name, setting in features.FLAGS.items():
        assert hasattr(constants, setting), name


def test_the_health_check_reports_every_flag(monkeypatch):
    monkeypatch.setattr(constants, "WORKSPACE_ROLES_ENABLED", True)
    monkeypatch.setattr(constants, "DIALER_IMPORT_ENABLED", False)
    public = features.public()
    assert set(public) == set(features.FLAGS)
    assert public["workspace_roles"] is True
    assert public["dialer_import"] is False


def test_decibyl_switches_are_reported_so_the_ui_learns_them_at_start_up(monkeypatch):
    # D-1a/D-1b shipped behind plain env switches with no registry entry, so
    # the UI could not know whether long tasks or private threads were on.
    monkeypatch.setattr(constants, "DECIBYL_LONG_TASKS_ENABLED", True)
    monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", False)
    public = features.public()
    assert public["decibyl_long_tasks"] is True
    assert public["decibyl_private_threads"] is False


def test_a_switched_off_feature_answers_404_and_an_on_one_passes(monkeypatch):
    check = features.require("agent_graph_extras")
    assert check.feature == "agent_graph_extras"
    monkeypatch.setattr(constants, "AGENT_GRAPH_EXTRAS_ENABLED", False)
    with pytest.raises(HTTPException) as caught:
        check()
    assert caught.value.status_code == 404
    monkeypatch.setattr(constants, "AGENT_GRAPH_EXTRAS_ENABLED", True)
    check()


def test_an_unknown_feature_is_a_mistake_caught_at_import():
    with pytest.raises(KeyError):
        features.require("no_such_feature")


@pytest.mark.asyncio
async def test_a_refusal_from_any_service_is_a_400_with_its_words():
    from api.app import app
    from api.services.dialer_import.connections import DialerConnectionError
    from api.services.workspace_roles import RoleError

    handler = app.exception_handlers[Refused]
    for error in (RoleError("Not in this workspace."), DialerConnectionError("x")):
        response = await handler(None, error)
        assert response.status_code == 400
        assert json.loads(response.body) == {"detail": str(error)}
