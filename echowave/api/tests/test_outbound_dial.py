"""dial_workflow: does it release the concurrency slot on every failure path.

A leaked slot is silent. Nothing fails at the time; an hour later the account
just cannot place calls. That is the whole reason this helper exists, so it is
what these tests check.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.services.call_concurrency import CallConcurrencyLimitError
from api.services.telephony import outbound


@pytest.fixture
def workflow():
    return SimpleNamespace(id=1, user_id=2, workflow_uuid="wf-uuid")


@pytest.fixture
def provider():
    p = MagicMock()
    p.PROVIDER_NAME = "plivo"
    p.WEBHOOK_ENDPOINT = "plivo/webhook"
    p.initiate_call = AsyncMock()
    return p


@pytest.fixture
def concurrency(monkeypatch):
    """Records what was acquired and released, so a leak is visible."""
    c = MagicMock()
    slot = SimpleNamespace(slot_id="slot-1")
    c.acquire_org_slot = AsyncMock(return_value=slot)
    c.bind_workflow_run = AsyncMock()
    c.release_slot = AsyncMock()
    c.release_workflow_run_slot = AsyncMock()
    monkeypatch.setattr(outbound, "call_concurrency", c)
    return c


@pytest.fixture
def db(monkeypatch):
    d = MagicMock()
    d.create_workflow_run = AsyncMock(return_value=SimpleNamespace(id=99))
    monkeypatch.setattr(outbound, "db_client", d)
    return d


@pytest.fixture
def quota(monkeypatch):
    fn = AsyncMock(return_value=SimpleNamespace(has_quota=True, error_message=None))
    monkeypatch.setattr(outbound, "authorize_workflow_run_start", fn)
    return fn


@pytest.fixture
def endpoints(monkeypatch):
    monkeypatch.setattr(
        outbound, "get_backend_endpoints", AsyncMock(return_value=("https://api", None))
    )


async def dial(workflow, provider, **kw):
    return await outbound.dial_workflow(
        workflow=workflow,
        organization_id=7,
        to_number="+919876543210",
        provider=provider,
        telephony_configuration_id=3,
        source="missed_call",
        **kw,
    )


@pytest.mark.asyncio
async def test_returns_the_run_id_on_success(
    workflow, provider, concurrency, db, quota, endpoints
):
    assert await dial(workflow, provider) == 99
    provider.initiate_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_slot_means_no_run_is_created(
    workflow, provider, concurrency, db, quota, endpoints
):
    concurrency.acquire_org_slot.side_effect = CallConcurrencyLimitError(
        organization_id=7, source="missed_call", wait_time=0.0, max_concurrent=1
    )
    with pytest.raises(outbound.NoConcurrencySlot):
        await dial(workflow, provider)
    db.create_workflow_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_slot_released_by_id_when_run_creation_fails(
    workflow, provider, concurrency, db, quota, endpoints
):
    """Before the run exists the slot is held by slot id, so it must be
    released that way. Releasing by run id here would leak it."""
    db.create_workflow_run.side_effect = RuntimeError("db down")
    with pytest.raises(RuntimeError):
        await dial(workflow, provider)
    concurrency.release_slot.assert_awaited_once()
    concurrency.release_workflow_run_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_slot_released_when_quota_refuses(
    workflow, provider, concurrency, db, quota, endpoints
):
    quota.return_value = SimpleNamespace(has_quota=False, error_message="no credit")
    with pytest.raises(outbound.QuotaExhausted):
        await dial(workflow, provider)
    concurrency.release_workflow_run_slot.assert_awaited_once_with(99)
    provider.initiate_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_slot_released_when_the_dial_fails(
    workflow, provider, concurrency, db, quota, endpoints
):
    provider.initiate_call.side_effect = RuntimeError("carrier refused")
    with pytest.raises(RuntimeError):
        await dial(workflow, provider)
    concurrency.release_workflow_run_slot.assert_awaited_once_with(99)


@pytest.mark.asyncio
async def test_quota_is_checked_after_the_run_exists(
    workflow, provider, concurrency, db, quota, endpoints
):
    """Hosted billing attaches its correlation id to the run, so the run has to
    exist before the quota call."""
    await dial(workflow, provider)
    assert quota.await_args.kwargs["workflow_run_id"] == 99


@pytest.mark.asyncio
async def test_extra_context_reaches_the_run(
    workflow, provider, concurrency, db, quota, endpoints
):
    await dial(workflow, provider, extra_context={"trigger_source": "missed_call"})
    ctx = db.create_workflow_run.await_args.kwargs["initial_context"]
    assert ctx["trigger_source"] == "missed_call"
    assert ctx["phone_number"] == "+919876543210"


@pytest.mark.asyncio
async def test_webhook_url_carries_the_ids_providers_need(
    workflow, provider, concurrency, db, quota, endpoints
):
    """Providers that build the media socket URL at dial time produce
    "None/None" without these, and the stream never connects."""
    await dial(workflow, provider)
    url = provider.initiate_call.await_args.kwargs["webhook_url"]
    assert "workflow_id=1" in url
    assert "workflow_run_id=99" in url
    assert "organization_id=7" in url
