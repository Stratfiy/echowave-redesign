from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services import quota_service

_UNSET = object()


def _workflow():
    return SimpleNamespace(
        id=7,
        user_id=123,
        organization_id=42,
    )


def _workflow_run(*, workflow_id: int = 7):
    return SimpleNamespace(id=88, workflow_id=workflow_id)


def _actor():
    return SimpleNamespace(id=456, provider_id="actor-456", selected_organization_id=42)


def _patch_workflow_context(
    monkeypatch,
    *,
    workflow=_UNSET,
    workflow_run=_UNSET,
    is_member: bool = True,
    balance: float = 5.0,
):
    workflow_value = _workflow() if workflow is _UNSET else workflow
    workflow_run_value = _workflow_run() if workflow_run is _UNSET else workflow_run
    monkeypatch.setattr(
        quota_service.db_client, "get_workflow", AsyncMock(return_value=workflow_value)
    )
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(return_value=workflow_run_value),
    )
    monkeypatch.setattr(
        quota_service.db_client,
        "is_user_member_of_organization",
        AsyncMock(return_value=is_member),
    )
    monkeypatch.setattr(
        quota_service.db_client, "get_balance", AsyncMock(return_value=balance)
    )


@pytest.mark.asyncio
async def test_authorize_denies_when_organization_id_missing():
    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=None,
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_not_found"


@pytest.mark.asyncio
async def test_authorize_denies_when_workflow_not_found(monkeypatch):
    _patch_workflow_context(monkeypatch, workflow=None)

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_not_found"


@pytest.mark.asyncio
async def test_authorize_fails_closed_when_workflow_lookup_errors(monkeypatch):
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow",
        AsyncMock(side_effect=RuntimeError("db down")),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_not_found"


@pytest.mark.asyncio
async def test_authorize_denies_when_actor_not_org_member(monkeypatch):
    _patch_workflow_context(monkeypatch, is_member=False)

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        actor_user=_actor(),
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_not_found"


@pytest.mark.asyncio
async def test_authorize_denies_when_workflow_run_missing(monkeypatch):
    _patch_workflow_context(monkeypatch, workflow_run=None)

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_run_not_found"


@pytest.mark.asyncio
async def test_authorize_denies_when_workflow_run_belongs_to_different_workflow(
    monkeypatch,
):
    _patch_workflow_context(monkeypatch, workflow_run=_workflow_run(workflow_id=999))

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_run_not_found"


@pytest.mark.asyncio
async def test_authorize_allows_run_with_sufficient_balance(monkeypatch):
    _patch_workflow_context(monkeypatch, balance=5.0)

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        actor_user=_actor(),
    )

    assert result.has_quota is True
    assert result.error_code == ""


@pytest.mark.asyncio
async def test_authorize_denies_run_below_minimum_balance(monkeypatch):
    _patch_workflow_context(monkeypatch, balance=0.01)

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is False
    assert result.error_code == "insufficient_credits"
    assert "/billing" in result.error_message


@pytest.mark.asyncio
async def test_authorize_allows_run_at_exactly_the_minimum_balance(monkeypatch):
    _patch_workflow_context(
        monkeypatch, balance=quota_service.MINIMUM_CALL_BALANCE_USD
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is True


@pytest.mark.asyncio
async def test_authorize_fails_closed_when_balance_lookup_errors(monkeypatch):
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_balance",
        AsyncMock(side_effect=RuntimeError("db down")),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is False
    assert result.error_code == "quota_check_failed"
