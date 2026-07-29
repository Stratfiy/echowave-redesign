from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from api.services import quota_service
from api.services.configuration.registry import ServiceProviders
from api.services.managed_model_services import MPS_CORRELATION_ID_CONTEXT_KEY

_UNSET = object()


def _decibyl_config(
    api_key: str = "mps_sk_12345678",
    *,
    managed_service_version: int = 1,
):
    return SimpleNamespace(
        managed_service_version=managed_service_version,
        llm=SimpleNamespace(provider=ServiceProviders.DECIBYL, api_key=api_key),
        stt=None,
        tts=None,
        embeddings=None,
    )


def _byok_config():
    return SimpleNamespace(
        managed_service_version=2,
        llm=SimpleNamespace(provider="openai", api_key="sk-openai"),
        stt=None,
        tts=None,
        embeddings=None,
    )


def _workflow():
    return SimpleNamespace(
        id=7,
        user_id=123,
        organization_id=42,
        workflow_configurations={"model_overrides": {}},
    )


def _workflow_owner():
    return SimpleNamespace(
        id=123,
        provider_id="provider-123",
    )


def _pinned_run(
    *,
    workflow_id: int = 7,
    workflow_configurations: dict | None = None,
):
    return SimpleNamespace(
        workflow_id=workflow_id,
        definition=SimpleNamespace(
            workflow_configurations=(
                workflow_configurations
                if workflow_configurations is not None
                else {"model_overrides": {}}
            ),
        ),
    )


def _patch_workflow_context(monkeypatch, *, workflow=_UNSET, owner=None):
    workflow_value = _workflow() if workflow is _UNSET else workflow
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow",
        AsyncMock(return_value=workflow_value),
    )
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_by_id",
        AsyncMock(
            side_effect=AssertionError("authorization must not use unscoped workflow")
        ),
    )
    monkeypatch.setattr(
        quota_service.db_client,
        "get_user_by_id",
        AsyncMock(return_value=owner or _workflow_owner()),
    )


# ---------------------------------------------------------------------------
# No prepaid-credit gating
# ---------------------------------------------------------------------------


def test_authorization_module_exposes_no_credit_gating():
    """Decibyl does not sell prepaid inference credits.

    Guards against a credit balance check being reintroduced into the run-start
    path, which would put an external billing service back on the critical path
    of every call.
    """
    for removed in (
        "MINIMUM_DECIBYL_CREDITS_FOR_CALL",
        "_authorize_hosted_workflow_run_start",
        "_authorize_oss_decibyl_keys",
    ):
        assert not hasattr(quota_service, removed), (
            f"{removed} should no longer exist on quota_service"
        )

    for removed in (
        "authorize_workflow_run_start",
        "check_service_key_usage",
        "report_platform_usage",
        "get_billing_pricing",
        "get_credit_ledger",
        "ensure_billing_account_v2",
        "create_credit_purchase_url",
        "get_usage_by_created_by",
    ):
        assert not hasattr(quota_service.mps_service_key_client, removed), (
            f"mps_service_key_client.{removed} should no longer exist"
        )


@pytest.mark.asyncio
async def test_byok_run_is_authorized_without_contacting_billing(monkeypatch):
    """A BYOK run needs no correlation id and no external call at all."""
    get_config = AsyncMock(return_value=_byok_config())
    create_correlation_id = AsyncMock()

    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "create_correlation_id",
        create_correlation_id,
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is True
    quota_service.db_client.get_workflow.assert_awaited_once_with(7, organization_id=42)
    get_config.assert_awaited_once_with(
        organization_id=42,
        workflow_configurations={"model_overrides": {}},
    )
    create_correlation_id.assert_not_awaited()


# ---------------------------------------------------------------------------
# Managed model services correlation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_managed_v2_run_mints_and_stores_correlation(monkeypatch):
    api_key = "mps_sk_12345678"
    workflow_run = SimpleNamespace(initial_context={"existing": "value"})
    create_correlation_id = AsyncMock(return_value={"correlation_id": "corr-123"})
    update_workflow_run = AsyncMock()

    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(return_value=_pinned_run()),
    )
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=workflow_run),
    )
    monkeypatch.setattr(
        quota_service.db_client,
        "update_workflow_run",
        update_workflow_run,
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(return_value=_decibyl_config(api_key, managed_service_version=2)),
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "create_correlation_id",
        create_correlation_id,
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is True
    quota_service.db_client.get_workflow_run.assert_awaited_once_with(
        88, organization_id=42
    )
    create_correlation_id.assert_awaited_once_with(
        service_key=api_key,
        workflow_run_id=88,
    )
    update_workflow_run.assert_awaited_once_with(
        88,
        initial_context={
            "existing": "value",
            MPS_CORRELATION_ID_CONTEXT_KEY: "corr-123",
        },
    )


@pytest.mark.asyncio
async def test_managed_v2_run_without_service_key_is_denied(monkeypatch):
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(return_value=_pinned_run()),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(return_value=_decibyl_config("", managed_service_version=2)),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is False
    assert result.error_code == "invalid_service_key"


@pytest.mark.asyncio
async def test_run_proceeds_when_model_gateway_is_unreachable(monkeypatch):
    """A transport failure minting a correlation id must not block the call."""
    request = httpx.Request(
        "POST",
        "https://services.decibyl.com/api/v1/service-keys/correlation-id/self",
    )

    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(return_value=_pinned_run()),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(return_value=_decibyl_config(managed_service_version=2)),
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "create_correlation_id",
        AsyncMock(
            side_effect=httpx.ConnectError("connection refused", request=request)
        ),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is True


@pytest.mark.asyncio
async def test_run_fails_closed_on_model_gateway_http_error(monkeypatch):
    request = httpx.Request(
        "POST",
        "https://services.decibyl.com/api/v1/service-keys/correlation-id/self",
    )
    response = httpx.Response(500, request=request)

    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(return_value=_pinned_run()),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(return_value=_decibyl_config(managed_service_version=2)),
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "create_correlation_id",
        AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "boom", request=request, response=response
            )
        ),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is False
    assert result.error_code == "quota_check_failed"


@pytest.mark.asyncio
async def test_run_fails_closed_when_storing_correlation_fails(monkeypatch):
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(return_value=_pinned_run()),
    )
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run_by_id",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(return_value=_decibyl_config(managed_service_version=2)),
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "create_correlation_id",
        AsyncMock(return_value={"correlation_id": "corr-123"}),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is False
    assert result.error_code == "quota_check_failed"


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authorize_workflow_run_rejects_actor_not_a_member(monkeypatch):
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "is_user_member_of_organization",
        AsyncMock(return_value=False),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        actor_user=SimpleNamespace(id=456, selected_organization_id=999),
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_not_found"


@pytest.mark.asyncio
async def test_authorize_workflow_run_membership_lookup_error_fails_closed(monkeypatch):
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "is_user_member_of_organization",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        actor_user=SimpleNamespace(id=456, selected_organization_id=42),
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_not_found"
    quota_service.db_client.get_user_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorize_workflow_run_allows_invited_member(monkeypatch):
    """User invited to an org can start workflows belonging to that org."""
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "is_user_member_of_organization",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(return_value=_byok_config()),
    )

    # actor_user.selected_organization_id=999 differs from workflow.organization_id=42,
    # but is_user_member_of_organization returns True so the run should be allowed.
    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        actor_user=SimpleNamespace(id=456, selected_organization_id=999),
    )

    assert result.has_quota is True


@pytest.mark.asyncio
async def test_authorize_workflow_run_requires_organization_scope(monkeypatch):
    _patch_workflow_context(monkeypatch)
    is_member_mock = AsyncMock()
    monkeypatch.setattr(
        quota_service.db_client,
        "is_user_member_of_organization",
        is_member_mock,
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(return_value=_byok_config()),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=None,
        actor_user=SimpleNamespace(id=456),
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_not_found"
    quota_service.db_client.get_workflow.assert_not_awaited()
    is_member_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorize_workflow_run_rejects_workflow_outside_org(monkeypatch):
    _patch_workflow_context(monkeypatch, workflow=None)
    is_member_mock = AsyncMock()
    monkeypatch.setattr(
        quota_service.db_client,
        "is_user_member_of_organization",
        is_member_mock,
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=99,
        actor_user=SimpleNamespace(id=456),
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_not_found"
    quota_service.db_client.get_workflow.assert_awaited_once_with(7, organization_id=99)
    is_member_mock.assert_not_awaited()
    quota_service.db_client.get_user_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorize_workflow_run_denies_when_run_missing(monkeypatch):
    get_config = AsyncMock()

    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_run_not_found"
    get_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorize_workflow_run_denies_run_bound_to_other_workflow(monkeypatch):
    get_config = AsyncMock()

    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(return_value=_pinned_run(workflow_id=999)),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_run_not_found"
    get_config.assert_not_awaited()


# ---------------------------------------------------------------------------
# Configuration resolution and fail-closed behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authorize_workflow_run_resolves_config_from_pinned_definition(
    monkeypatch,
):
    """The correlation must be minted for the config the run will execute.

    workflow.workflow_configurations is synced to the draft on save, while the
    run executes its pinned definition — if the draft carries a different
    Decibyl service key, minting from the workflow column binds the correlation
    to a key the run never uses and the model gateway rejects every call.
    """
    draft_configs = {"model_configuration_v2_override": {"key": "draft"}}
    pinned_configs = {"model_configuration_v2_override": {"key": "published"}}
    workflow = _workflow()
    workflow.workflow_configurations = draft_configs

    get_config = AsyncMock(return_value=_byok_config())

    _patch_workflow_context(monkeypatch, workflow=workflow)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(return_value=_pinned_run(workflow_configurations=pinned_configs)),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is True
    get_config.assert_awaited_once_with(
        organization_id=42,
        workflow_configurations=pinned_configs,
    )


@pytest.mark.asyncio
async def test_authorize_workflow_run_falls_back_to_workflow_configs_without_definition(
    monkeypatch,
):
    """Legacy runs without a pinned definition keep using the workflow column."""
    get_config = AsyncMock(return_value=_byok_config())

    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(return_value=SimpleNamespace(workflow_id=7, definition=None)),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is True
    get_config.assert_awaited_once_with(
        organization_id=42,
        workflow_configurations={"model_overrides": {}},
    )


@pytest.mark.asyncio
async def test_authorize_workflow_run_fails_closed_on_config_resolution_error(
    monkeypatch,
):
    """A config-resolution bug must deny the run, not fail open (issue #331)."""
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(side_effect=RuntimeError("configuration resolution bug")),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is False
    assert result.error_code == "quota_check_failed"


@pytest.mark.asyncio
async def test_authorize_workflow_run_fails_closed_on_user_lookup_error(monkeypatch):
    """A DB read failure on the owner lookup is a 'cannot verify' → denied."""
    get_config = AsyncMock()

    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_user_by_id",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is False
    assert result.error_code == "quota_check_failed"
    get_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorize_workflow_run_fails_closed_on_run_lookup_error(monkeypatch):
    """A DB read failure on the run lookup is a 'cannot verify' → denied."""
    get_config = AsyncMock()

    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is False
    assert result.error_code == "quota_check_failed"
    get_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorize_workflow_run_denies_when_owner_missing(monkeypatch):
    _patch_workflow_context(monkeypatch, owner=None)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_user_by_id",
        AsyncMock(return_value=None),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is False
    assert result.error_code == "user_not_found"
