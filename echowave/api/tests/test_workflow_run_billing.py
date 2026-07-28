from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services import workflow_run_billing as workflow_run_billing_mod
from api.services.workflow_run_billing import (
    _is_usage_not_ready_error,
    rate_and_record_platform_usage,
    report_completed_workflow_run_platform_usage,
    report_workflow_run_platform_usage,
)


def _make_workflow_run():
    return SimpleNamespace(
        id=123,
        workflow_id=456,
        is_completed=True,
        initial_context={"mps_correlation_id": "mps-corr-123"},
        usage_info={"call_duration_seconds": 87},
        workflow=SimpleNamespace(
            organization_id=42,
            user=SimpleNamespace(selected_organization_id=42),
        ),
    )


def test_is_usage_not_ready_error_detects_mps_409():
    exc = Exception("Failed to report platform usage")
    exc.response = SimpleNamespace(
        status_code=409,
        text='{"detail":"usage_not_ready"}',
    )

    assert _is_usage_not_ready_error(exc) is True


@pytest.mark.asyncio
async def test_report_workflow_run_platform_usage_reports_hosted_completion(
    monkeypatch,
):
    workflow_run = _make_workflow_run()
    report_usage = AsyncMock(return_value={"metered": True})

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_workflow_run_platform_usage(workflow_run)

    report_usage.assert_awaited_once_with(
        organization_id=42,
        correlation_id="mps-corr-123",
        duration_seconds=None,
        workflow_run_id=workflow_run.id,
        metadata={
            "source": "workflow_run_completion",
            "workflow_id": workflow_run.workflow_id,
            "duration_source": "mps_correlation",
        },
    )


@pytest.mark.asyncio
async def test_report_workflow_run_platform_usage_reports_duration_without_correlation(
    monkeypatch,
):
    workflow_run = _make_workflow_run()
    workflow_run.initial_context = {}
    report_usage = AsyncMock(return_value={"metered": True})

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_workflow_run_platform_usage(workflow_run)

    report_usage.assert_awaited_once_with(
        organization_id=42,
        correlation_id=None,
        duration_seconds=87.0,
        workflow_run_id=workflow_run.id,
        metadata={
            "source": "workflow_run_completion",
            "workflow_id": workflow_run.workflow_id,
            "duration_source": "dograh_usage_info",
        },
    )


@pytest.mark.asyncio
async def test_report_workflow_run_platform_usage_skips_missing_duration_without_correlation(
    monkeypatch,
):
    workflow_run = _make_workflow_run()
    workflow_run.initial_context = {}
    workflow_run.usage_info = {}
    report_usage = AsyncMock()

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_workflow_run_platform_usage(workflow_run)

    report_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_workflow_run_platform_usage_skips_oss(monkeypatch):
    workflow_run = _make_workflow_run()
    report_usage = AsyncMock()

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "oss")
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_workflow_run_platform_usage(workflow_run)

    report_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_workflow_run_platform_usage_skips_incomplete(monkeypatch):
    workflow_run = _make_workflow_run()
    workflow_run.is_completed = False
    report_usage = AsyncMock()

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_workflow_run_platform_usage(workflow_run)

    report_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_completed_workflow_run_platform_usage_loads_run(monkeypatch):
    workflow_run = _make_workflow_run()
    get_run = AsyncMock(return_value=workflow_run)
    report_usage = AsyncMock(return_value={"metered": True})

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_workflow_run_by_id",
        get_run,
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_completed_workflow_run_platform_usage(workflow_run.id)

    get_run.assert_awaited_once_with(workflow_run.id)
    report_usage.assert_awaited_once()


def _rateable_run(*, cost_info=None, duration_seconds=87):
    return SimpleNamespace(
        id=123,
        workflow_id=456,
        is_completed=True,
        cost_info=cost_info,
        usage_info={"call_duration_seconds": duration_seconds},
        workflow=SimpleNamespace(organization_id=42),
    )


@pytest.mark.asyncio
async def test_rate_and_record_platform_usage_charges_default_rate(monkeypatch):
    workflow_run = _rateable_run()
    update_run = AsyncMock()
    record_entry = AsyncMock()
    record_run_usage = AsyncMock()

    monkeypatch.setattr(
        workflow_run_billing_mod, "PLATFORM_RATE_USD_PER_MINUTE", 0.02
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=workflow_run),
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_organization_by_id",
        AsyncMock(return_value=SimpleNamespace(price_per_second_usd=None)),
    )
    monkeypatch.setattr(workflow_run_billing_mod.db_client, "update_workflow_run", update_run)
    monkeypatch.setattr(workflow_run_billing_mod.db_client, "record_entry", record_entry)
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client, "record_run_usage", record_run_usage
    )

    await rate_and_record_platform_usage(workflow_run.id)

    expected_charge = round(87 * (0.02 / 60.0), 4)
    update_run.assert_awaited_once()
    _, kwargs = update_run.call_args
    assert kwargs["cost_info"]["charge_usd"] == expected_charge
    record_entry.assert_awaited_once_with(
        42,
        "charge",
        -expected_charge,
        workflow_run_id=workflow_run.id,
        description="Platform usage: 87s",
    )
    record_run_usage.assert_awaited_once_with(
        42, duration_seconds=87.0, amount_usd=expected_charge
    )


@pytest.mark.asyncio
async def test_rate_and_record_platform_usage_uses_org_rate_override(monkeypatch):
    workflow_run = _rateable_run()
    record_entry = AsyncMock()

    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=workflow_run),
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_organization_by_id",
        AsyncMock(return_value=SimpleNamespace(price_per_second_usd=0.001)),
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client, "update_workflow_run", AsyncMock()
    )
    monkeypatch.setattr(workflow_run_billing_mod.db_client, "record_entry", record_entry)
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client, "record_run_usage", AsyncMock()
    )

    await rate_and_record_platform_usage(workflow_run.id)

    expected_charge = round(87 * 0.001, 4)
    record_entry.assert_awaited_once_with(
        42,
        "charge",
        -expected_charge,
        workflow_run_id=workflow_run.id,
        description="Platform usage: 87s",
    )


@pytest.mark.asyncio
async def test_rate_and_record_platform_usage_is_idempotent(monkeypatch):
    workflow_run = _rateable_run(cost_info={"charge_usd": 0.05})
    record_entry = AsyncMock()

    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=workflow_run),
    )
    monkeypatch.setattr(workflow_run_billing_mod.db_client, "record_entry", record_entry)

    await rate_and_record_platform_usage(workflow_run.id)

    record_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_and_record_platform_usage_skips_incomplete_run(monkeypatch):
    workflow_run = _rateable_run()
    workflow_run.is_completed = False
    record_entry = AsyncMock()

    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=workflow_run),
    )
    monkeypatch.setattr(workflow_run_billing_mod.db_client, "record_entry", record_entry)

    await rate_and_record_platform_usage(workflow_run.id)

    record_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_and_record_platform_usage_skips_missing_duration(monkeypatch):
    workflow_run = _rateable_run()
    workflow_run.usage_info = {}
    record_entry = AsyncMock()

    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=workflow_run),
    )
    monkeypatch.setattr(workflow_run_billing_mod.db_client, "record_entry", record_entry)

    await rate_and_record_platform_usage(workflow_run.id)

    record_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_and_record_platform_usage_skips_missing_run(monkeypatch):
    record_entry = AsyncMock()

    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(workflow_run_billing_mod.db_client, "record_entry", record_entry)

    await rate_and_record_platform_usage(999)

    record_entry.assert_not_awaited()
