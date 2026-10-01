"""Real FastAPI route contracts with isolated external service dependencies.

Import the actual route source under a private module name. Only its DB,
authentication and general builder runtime imports are replaced; request
validation, routing, metering order, exception mapping and response serialization
all execute the production handler. No application environment or database is
loaded, and module replacements are restored before each test runs.
"""

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import api.services.agent_builder as builder_package
from api.services.agent_builder.client import BuilderClientError


def module(name, **attributes):
    result = ModuleType(name)
    result.__dict__.update(attributes)
    return result


@pytest.fixture
def endpoint(monkeypatch):
    events = []
    user = SimpleNamespace(id=5, selected_organization_id=7)
    session = SimpleNamespace()

    async def get_user():
        return user

    async def get_workflow(workflow_id, *, organization_id):
        events.append("authorize")
        return SimpleNamespace(id=workflow_id, organization_id=organization_id)

    context = AsyncMock()
    context.__aenter__.return_value = session
    db_client = SimpleNamespace(
        get_workflow=AsyncMock(side_effect=get_workflow),
        async_session=Mock(return_value=context),
        update_workflow=AsyncMock(),
        save_workflow_draft=AsyncMock(),
    )

    class BuilderUnavailable(RuntimeError):
        pass

    model = SimpleNamespace(provider="test", model="test", api_key="unused")

    async def resolve_model(_session):
        events.append("resolve")
        return model

    resolve_model_mock = AsyncMock(side_effect=resolve_model)

    async def resolve_for_organization(session, _choice, *, organization_id):
        # BYOK-1: the route asks for the account's model; with the account
        # holding no key of its own that is the platform model, as before.
        return await resolve_model_mock(session)

    settings = module(
        "api.services.agent_builder.settings",
        BuilderUnavailable=BuilderUnavailable,
        resolve_model=resolve_model_mock,
        resolve_for_organization=resolve_for_organization,
    )
    limits = module(
        "api.services.agent_builder.limits",
        LimitState=SimpleNamespace,
        PAST_ALLOWANCE_CREDITS=5,
    )
    replacements = {
        "api.db": module("api.db", db_client=db_client),
        "api.db.models": module("api.db.models", UserModel=SimpleNamespace),
        "api.services.agent_builder.settings": settings,
        "api.services.agent_builder.limits": limits,
        "api.services.agent_builder.session": module(
            "api.services.agent_builder.session", run_turn=AsyncMock()
        ),
        "api.services.auth.depends": module(
            "api.services.auth.depends", get_user=get_user
        ),
    }
    path = Path(__file__).resolve().parents[1] / "routes" / "agent_builder.py"
    spec = importlib.util.spec_from_file_location("_editor_route_contract_test", path)
    route = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as imports:
        for name, replacement in replacements.items():
            imports.setitem(sys.modules, name, replacement)
        imports.setattr(builder_package, "settings", settings, raising=False)
        imports.setattr(builder_package, "limits", limits, raising=False)
        spec.loader.exec_module(route)

    state = SimpleNamespace(
        used=1,
        limit=20,
        remaining=19,
        resets_at=datetime(2026, 10, 1, tzinfo=UTC),
        past_allowance=False,
    )

    async def meter(_session, organization_id):
        events.append("meter")
        assert _session is session and organization_id == 7
        return state, 0

    async def propose(*, model, graph, message):
        events.append("propose")
        return {
            "status": "clarification",
            "summary": "Which wording?",
            "graph": None,
            "changes": [],
        }

    route.meter_builder_message = AsyncMock(side_effect=meter)
    route.propose_edit = AsyncMock(side_effect=propose)
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    with TestClient(app) as client:
        yield SimpleNamespace(
            route=route,
            client=client,
            user=user,
            model=model,
            events=events,
            db=db_client,
            app=app,
        )


def payload():
    return {
        "workflow_id": 39,
        "message": "Edit wording",
        "graph": {
            "nodes": [{"id": "1", "type": "agentNode", "data": {"prompt": "Hello"}}],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        },
    }


def post(endpoint, body=None):
    return endpoint.client.post(
        "/api/v1/agent-builder/edit-proposal", json=payload() if body is None else body
    )


def test_foreign_agent_rejected_before_spend(endpoint):
    endpoint.db.get_workflow.side_effect = None
    endpoint.db.get_workflow.return_value = None
    response = post(endpoint)
    assert response.status_code == 404
    endpoint.db.get_workflow.assert_awaited_once_with(39, organization_id=7)
    endpoint.route.meter_builder_message.assert_not_awaited()
    endpoint.route.propose_edit.assert_not_awaited()
    endpoint.db.async_session.assert_not_called()


def test_missing_workspace_rejected_before_read_or_spend(endpoint):
    endpoint.user.selected_organization_id = None
    assert post(endpoint).status_code == 400
    endpoint.db.get_workflow.assert_not_awaited()
    endpoint.route.meter_builder_message.assert_not_awaited()


def test_success_orders_authorization_and_spend_without_writes(endpoint):
    response = post(endpoint)
    assert response.status_code == 200
    assert endpoint.events == ["authorize", "resolve", "meter", "propose"]
    assert response.json()["status"] == "clarification"
    assert response.json()["usage"]["charged_credits"] == 0
    endpoint.route.propose_edit.assert_awaited_once_with(
        model=endpoint.model, graph=payload()["graph"], message="Edit wording"
    )
    endpoint.db.update_workflow.assert_not_awaited()
    endpoint.db.save_workflow_draft.assert_not_awaited()


def test_proposal_response_retains_graph_and_changes(endpoint):
    result = {
        "status": "proposal",
        "summary": "Updated wording",
        "graph": payload()["graph"],
        "changes": [
            {"node_id": "1", "field": "prompt", "before": "Hi", "after": "Hello"}
        ],
    }
    endpoint.route.propose_edit.side_effect = None
    endpoint.route.propose_edit.return_value = result
    response = post(endpoint)
    assert response.status_code == 200
    assert all(response.json()[key] == value for key, value in result.items())
    endpoint.db.update_workflow.assert_not_awaited()


def test_provider_unavailable_does_not_charge(endpoint):
    endpoint.route.settings.resolve_model.side_effect = (
        endpoint.route.settings.BuilderUnavailable("Not configured")
    )
    response = post(endpoint)
    assert response.status_code == 503
    endpoint.route.meter_builder_message.assert_not_awaited()
    endpoint.route.propose_edit.assert_not_awaited()


def test_insufficient_allowance_does_not_call_model(endpoint):
    endpoint.route.meter_builder_message.side_effect = HTTPException(
        status_code=402, detail="No credit"
    )
    assert post(endpoint).status_code == 402
    endpoint.route.propose_edit.assert_not_awaited()


def test_model_failure_returns_actionable_error_without_writes(endpoint):
    endpoint.route.propose_edit.side_effect = BuilderClientError(
        "Try again; draft unchanged"
    )
    response = post(endpoint)
    assert response.status_code == 502
    assert response.json()["detail"] == "Try again; draft unchanged"
    endpoint.route.meter_builder_message.assert_awaited_once()
    endpoint.db.update_workflow.assert_not_awaited()


@pytest.mark.parametrize(
    "patch",
    [
        {"workflow_id": 0},
        {"message": " "},
        {"message": "x" * 4001},
        {"graph": {"nodes": [], "edges": []}},
        {"unexpected": "ignored?"},
    ],
)
def test_request_validation_precedes_spend(endpoint, patch):
    assert post(endpoint, {**payload(), **patch}).status_code == 422
    endpoint.db.get_workflow.assert_not_awaited()
    endpoint.route.meter_builder_message.assert_not_awaited()
    endpoint.route.propose_edit.assert_not_awaited()


def test_openapi_exposes_real_request_contract(endpoint):
    schema = endpoint.app.openapi()
    operation = schema["paths"]["/api/v1/agent-builder/edit-proposal"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/EditProposalRequest")
    fields = schema["components"]["schemas"]["EditProposalRequest"]["properties"]
    assert fields["message"]["maxLength"] == 4000
    assert set(fields) == {"workflow_id", "message", "graph"}
