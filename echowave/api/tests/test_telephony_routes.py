from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.db.workflow_run_client import WorkflowRunStateConflictError
from api.enums import WorkflowRunMode, WorkflowRunState
from api.errors.telephony_errors import TelephonyError
from api.routes.telephony import _handle_telephony_websocket, handle_inbound_run, router
from api.services.auth.depends import get_user
from api.services.call_concurrency import CallConcurrencyLimitError


@pytest.fixture(autouse=True)
def calling_window_open(monkeypatch):
    """Hold the TCCCPR calling window open for this module.

    These tests are about call setup, not about the window — but the route
    checks the window immediately before dialling, using the wall clock. Left
    alone, every test here passes between 09:00 and 21:00 IST and returns 451
    the rest of the day: green all afternoon, red all evening, and looking
    exactly like a flake rather than a timezone.

    The window itself is covered properly in test_dnd_gate.py, which pins
    ``now`` instead of stubbing it.
    """
    monkeypatch.setattr(
        "api.services.compliance.dnd.within_calling_hours",
        lambda **kwargs: True,
    )


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=7,
        selected_organization_id=11,
    )
    return app


def _workflow(*, workflow_id: int = 33, user_id: int = 99):
    return SimpleNamespace(
        id=workflow_id,
        user_id=user_id,
        organization_id=11,
        template_context_variables={"template_key": "template-value"},
    )


def _provider():
    return SimpleNamespace(
        PROVIDER_NAME="twilio",
        WEBHOOK_ENDPOINT="twilio/voice",
        validate_config=Mock(return_value=True),
        initiate_call=AsyncMock(
            return_value=SimpleNamespace(
                caller_number="+15550001111",
                provider_metadata={"call_id": "call-123"},
            )
        ),
    )


def test_initiate_call_executes_as_workflow_owner_for_shared_org_workflow():
    app = _make_test_app()
    client = TestClient(app)

    workflow = _workflow()
    provider = _provider()
    quota_mock = AsyncMock(
        return_value=SimpleNamespace(has_quota=True, error_message="")
    )

    with (
        patch("api.routes.telephony.db_client") as mock_db,
        patch("api.routes.telephony.call_concurrency") as mock_concurrency,
        # The rental-suspension gate reads the config's default caller ID.
        # These tests mock every dependency of the route so nothing touches a
        # real database; this is one more of them.
        patch(
            "api.routes.telephony.number_lifecycle.assert_configuration_may_serve",
            new=AsyncMock(return_value=None),
        ),
        # The provider-key gate resolves the account's model configuration
        # through its own clients, not the one patched above. Stubbed for the
        # same reason as the suspension gate; it is covered in
        # test_byok_key_readiness.py.
        patch(
            "api.routes.telephony.key_readiness.assert_workflow_may_run",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "api.routes.telephony.authorize_workflow_run_start",
            new=quota_mock,
        ),
        patch(
            "api.routes.telephony.get_default_telephony_provider",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "api.routes.telephony.get_backend_endpoints",
            new=AsyncMock(return_value=("https://api.example.com", "wss://ignored")),
        ),
    ):
        slot = object()
        mock_concurrency.acquire_org_slot = AsyncMock(return_value=slot)
        mock_concurrency.bind_workflow_run = AsyncMock()
        mock_concurrency.release_slot = AsyncMock()
        mock_concurrency.release_workflow_run_slot = AsyncMock()

        mock_db.get_user_configurations = AsyncMock(
            return_value=SimpleNamespace(test_phone_number=None)
        )
        # The TCCCPR gate reads the do-not-disturb list through this same
        # client, and fails closed when it cannot. Unstubbed, every one of
        # these tests refuses its own call with a 451.
        mock_db.is_number_dnd_listed = AsyncMock(return_value=False)
        # Whether the destination is one this account has verified. The route
        # reads it through the same client, both to decide if an unverified
        # number may be dialled at all and to decide whether the calling window
        # applies. None here means "not verified", which is the stricter path:
        # these tests keep the window (held open by the fixture above) rather
        # than taking the exemption.
        mock_db.get_verified_number = AsyncMock(return_value=None)
        mock_db.get_default_telephony_configuration = AsyncMock(
            return_value=SimpleNamespace(id=55)
        )
        # Bring-your-own telephony, so the KYC gate does not apply. These tests
        # are about call setup; the gate has its own suite.
        mock_db.get_telephony_configuration_for_org = AsyncMock(
            return_value=SimpleNamespace(id=55, is_platform_managed=False)
        )
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.create_workflow_run = AsyncMock(
            return_value=SimpleNamespace(
                id=501,
                name="WR-TEL-OUT-00000001",
                initial_context={"template_key": "template-value"},
            )
        )
        mock_db.update_workflow_run = AsyncMock()

        response = client.post(
            "/telephony/initiate-call",
            json={"workflow_id": workflow.id, "phone_number": "+15551234567"},
        )

    assert response.status_code == 200
    quota_mock.assert_awaited_once_with(
        workflow_id=workflow.id,
        organization_id=workflow.organization_id,
        workflow_run_id=501,
        actor_user=ANY,
    )
    mock_db.get_workflow.assert_awaited_once_with(workflow.id, organization_id=11)

    create_call = mock_db.create_workflow_run.await_args
    create_args = create_call.args
    create_kwargs = create_call.kwargs
    assert create_args[1] == workflow.id
    assert create_kwargs["user_id"] == workflow.user_id
    assert create_kwargs["organization_id"] == workflow.organization_id
    assert create_kwargs["initial_context"]["template_key"] == "template-value"
    # This org dials on its own carrier, so no shared-pool slot is taken. The
    # scope arguments are asserted as None rather than omitted: they are what
    # bounds Decibyl's own caller-ID pool, and an account with its own carrier
    # counting against that pool would let one customer's traffic exhaust the
    # trial line for everybody.
    mock_concurrency.acquire_org_slot.assert_awaited_once_with(
        workflow.organization_id,
        source="telephony_outbound",
        timeout=0,
        scope_key=None,
        scope_max_concurrent=None,
    )
    mock_concurrency.bind_workflow_run.assert_awaited_once_with(slot, 501)

    initiate_kwargs = provider.initiate_call.await_args.kwargs
    assert initiate_kwargs["workflow_id"] == workflow.id
    # The media websocket URL is keyed on the org, not the workflow owner.
    assert initiate_kwargs["organization_id"] == workflow.organization_id
    webhook_url = initiate_kwargs["webhook_url"]
    assert f"organization_id={workflow.organization_id}" in webhook_url
    # The answer URL carries no workflow owner: nothing downstream scopes on it.
    assert "user_id=" not in webhook_url
    mock_db.get_user_configurations.assert_not_called()


def test_initiate_call_uses_organization_preference_phone_number():
    app = _make_test_app()
    client = TestClient(app)

    workflow = _workflow()
    provider = _provider()
    quota_mock = AsyncMock(
        return_value=SimpleNamespace(has_quota=True, error_message="")
    )

    with (
        patch("api.routes.telephony.db_client") as mock_db,
        patch("api.routes.telephony.call_concurrency") as mock_concurrency,
        # The rental-suspension gate reads the config's default caller ID.
        # These tests mock every dependency of the route so nothing touches a
        # real database; this is one more of them.
        patch(
            "api.routes.telephony.number_lifecycle.assert_configuration_may_serve",
            new=AsyncMock(return_value=None),
        ),
        # The provider-key gate resolves the account's model configuration
        # through its own clients, not the one patched above. Stubbed for the
        # same reason as the suspension gate; it is covered in
        # test_byok_key_readiness.py.
        patch(
            "api.routes.telephony.key_readiness.assert_workflow_may_run",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "api.routes.telephony.authorize_workflow_run_start",
            new=quota_mock,
        ),
        patch(
            "api.routes.telephony.get_default_telephony_provider",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "api.routes.telephony.get_backend_endpoints",
            new=AsyncMock(return_value=("https://api.example.com", "wss://ignored")),
        ),
    ):
        mock_concurrency.acquire_org_slot = AsyncMock(return_value=object())
        mock_concurrency.bind_workflow_run = AsyncMock()
        mock_concurrency.release_slot = AsyncMock()
        mock_concurrency.release_workflow_run_slot = AsyncMock()

        mock_db.get_user_configurations = AsyncMock(
            return_value=SimpleNamespace(test_phone_number="+15550000000")
        )
        mock_db.get_configuration = Mock(
            return_value=SimpleNamespace(value={"test_phone_number": "+15557654321"})
        )
        # The TCCCPR gate reads the do-not-disturb list through this same
        # client, and fails closed when it cannot. Unstubbed, every one of
        # these tests refuses its own call with a 451.
        mock_db.is_number_dnd_listed = AsyncMock(return_value=False)
        # Whether the destination is one this account has verified. The route
        # reads it through the same client, both to decide if an unverified
        # number may be dialled at all and to decide whether the calling window
        # applies. None here means "not verified", which is the stricter path:
        # these tests keep the window (held open by the fixture above) rather
        # than taking the exemption.
        mock_db.get_verified_number = AsyncMock(return_value=None)
        mock_db.get_default_telephony_configuration = AsyncMock(
            return_value=SimpleNamespace(id=55)
        )
        # Bring-your-own telephony, so the KYC gate does not apply. These tests
        # are about call setup; the gate has its own suite.
        mock_db.get_telephony_configuration_for_org = AsyncMock(
            return_value=SimpleNamespace(id=55, is_platform_managed=False)
        )
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.create_workflow_run = AsyncMock(
            return_value=SimpleNamespace(
                id=501,
                name="WR-TEL-OUT-00000001",
                initial_context={},
            )
        )
        mock_db.update_workflow_run = AsyncMock()

        response = client.post(
            "/telephony/initiate-call",
            json={"workflow_id": workflow.id},
        )

    assert response.status_code == 200
    assert provider.initiate_call.await_args.kwargs["to_number"] == "+15557654321"
    mock_db.get_user_configurations.assert_not_called()


def test_initiate_call_rejects_existing_run_for_different_workflow():
    app = _make_test_app()
    client = TestClient(app)

    workflow = _workflow()
    provider = _provider()
    quota_mock = AsyncMock(
        return_value=SimpleNamespace(has_quota=True, error_message="")
    )

    with (
        patch("api.routes.telephony.db_client") as mock_db,
        patch("api.routes.telephony.call_concurrency") as mock_concurrency,
        # The rental-suspension gate reads the config's default caller ID.
        # These tests mock every dependency of the route so nothing touches a
        # real database; this is one more of them.
        patch(
            "api.routes.telephony.number_lifecycle.assert_configuration_may_serve",
            new=AsyncMock(return_value=None),
        ),
        # The provider-key gate resolves the account's model configuration
        # through its own clients, not the one patched above. Stubbed for the
        # same reason as the suspension gate; it is covered in
        # test_byok_key_readiness.py.
        patch(
            "api.routes.telephony.key_readiness.assert_workflow_may_run",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "api.routes.telephony.authorize_workflow_run_start",
            new=quota_mock,
        ),
        patch(
            "api.routes.telephony.get_default_telephony_provider",
            new=AsyncMock(return_value=provider),
        ),
    ):
        mock_concurrency.acquire_org_slot = AsyncMock(return_value=object())
        mock_concurrency.bind_workflow_run = AsyncMock()
        mock_concurrency.release_slot = AsyncMock()
        mock_concurrency.release_workflow_run_slot = AsyncMock()

        mock_db.get_user_configurations = AsyncMock(
            return_value=SimpleNamespace(test_phone_number=None)
        )
        # The TCCCPR gate reads the do-not-disturb list through this same
        # client, and fails closed when it cannot. Unstubbed, every one of
        # these tests refuses its own call with a 451.
        mock_db.is_number_dnd_listed = AsyncMock(return_value=False)
        # Whether the destination is one this account has verified. The route
        # reads it through the same client, both to decide if an unverified
        # number may be dialled at all and to decide whether the calling window
        # applies. None here means "not verified", which is the stricter path:
        # these tests keep the window (held open by the fixture above) rather
        # than taking the exemption.
        mock_db.get_verified_number = AsyncMock(return_value=None)
        mock_db.get_default_telephony_configuration = AsyncMock(
            return_value=SimpleNamespace(id=55)
        )
        # Bring-your-own telephony, so the KYC gate does not apply. These tests
        # are about call setup; the gate has its own suite.
        mock_db.get_telephony_configuration_for_org = AsyncMock(
            return_value=SimpleNamespace(id=55, is_platform_managed=False)
        )
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.get_workflow_run = AsyncMock(
            return_value=SimpleNamespace(
                id=501,
                workflow_id=44,
                name="WR-TEL-OUT-00000044",
                initial_context={},
            )
        )

        response = client.post(
            "/telephony/initiate-call",
            json={
                "workflow_id": workflow.id,
                "workflow_run_id": 501,
                "phone_number": "+15551234567",
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "workflow_run_workflow_mismatch"
    mock_db.get_workflow_run.assert_awaited_once_with(501, organization_id=11)
    mock_concurrency.release_slot.assert_awaited_once()
    assert not mock_db.create_workflow_run.called
    assert provider.initiate_call.await_count == 0


def test_initiate_call_rejects_when_concurrency_limit_reached():
    app = _make_test_app()
    client = TestClient(app)

    workflow = _workflow()
    provider = _provider()

    with (
        patch("api.routes.telephony.db_client") as mock_db,
        patch("api.routes.telephony.call_concurrency") as mock_concurrency,
        # The rental-suspension gate reads the config's default caller ID.
        # These tests mock every dependency of the route so nothing touches a
        # real database; this is one more of them.
        patch(
            "api.routes.telephony.number_lifecycle.assert_configuration_may_serve",
            new=AsyncMock(return_value=None),
        ),
        # The provider-key gate resolves the account's model configuration
        # through its own clients, not the one patched above. Stubbed for the
        # same reason as the suspension gate; it is covered in
        # test_byok_key_readiness.py.
        patch(
            "api.routes.telephony.key_readiness.assert_workflow_may_run",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "api.routes.telephony.get_default_telephony_provider",
            new=AsyncMock(return_value=provider),
        ),
    ):
        mock_concurrency.acquire_org_slot = AsyncMock(
            side_effect=CallConcurrencyLimitError(
                organization_id=workflow.organization_id,
                source="telephony_outbound",
                wait_time=0,
                max_concurrent=1,
            )
        )
        # The TCCCPR gate reads the do-not-disturb list through this same
        # client, and fails closed when it cannot. Unstubbed, every one of
        # these tests refuses its own call with a 451.
        mock_db.is_number_dnd_listed = AsyncMock(return_value=False)
        # Whether the destination is one this account has verified. The route
        # reads it through the same client, both to decide if an unverified
        # number may be dialled at all and to decide whether the calling window
        # applies. None here means "not verified", which is the stricter path:
        # these tests keep the window (held open by the fixture above) rather
        # than taking the exemption.
        mock_db.get_verified_number = AsyncMock(return_value=None)
        mock_db.get_default_telephony_configuration = AsyncMock(
            return_value=SimpleNamespace(id=55)
        )
        # Bring-your-own telephony, so the KYC gate does not apply. These tests
        # are about call setup; the gate has its own suite.
        mock_db.get_telephony_configuration_for_org = AsyncMock(
            return_value=SimpleNamespace(id=55, is_platform_managed=False)
        )
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.create_workflow_run = AsyncMock()

        response = client.post(
            "/telephony/initiate-call",
            json={"workflow_id": workflow.id, "phone_number": "+15551234567"},
        )

    assert response.status_code == 429
    assert response.json()["detail"] == "Concurrent call limit reached"
    mock_db.create_workflow_run.assert_not_called()
    provider.initiate_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_inbound_run_rejects_when_concurrency_limit_reached():
    request = SimpleNamespace(headers={}, url="https://api.example.com/inbound/run")
    provider_class = SimpleNamespace(
        PROVIDER_NAME="twilio",
        generate_validation_error_response=Mock(return_value="limit-response"),
    )
    normalized_data = SimpleNamespace(
        provider="twilio",
        direction="inbound",
        to_number="+15551230000",
        from_number="+15557650000",
        to_country="US",
        from_country="US",
        account_id="acct-1",
        call_id="call-1",
        raw_data={},
    )
    config = SimpleNamespace(id=55, organization_id=11)
    phone_row = SimpleNamespace(id=77, inbound_workflow_id=33)
    workflow = SimpleNamespace(id=33, user_id=99)
    provider_instance = SimpleNamespace(
        verify_inbound_signature=AsyncMock(return_value=True)
    )

    with (
        patch(
            "api.routes.telephony.parse_webhook_request",
            new=AsyncMock(return_value=({}, "raw-body")),
        ),
        patch(
            "api.routes.telephony._detect_provider",
            new=AsyncMock(return_value=provider_class),
        ),
        patch(
            "api.routes.telephony.normalize_webhook_data",
            return_value=normalized_data,
        ),
        patch("api.routes.telephony.db_client") as mock_db,
        patch(
            "api.routes.telephony.get_telephony_provider_by_id",
            new=AsyncMock(return_value=provider_instance),
        ),
        patch("api.routes.telephony.call_concurrency") as mock_concurrency,
    ):
        mock_db.find_inbound_route_by_account = AsyncMock(
            return_value=(config, phone_row)
        )
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.create_workflow_run = AsyncMock()
        mock_concurrency.acquire_org_slot = AsyncMock(
            side_effect=CallConcurrencyLimitError(
                organization_id=config.organization_id,
                source="inbound:twilio",
                wait_time=0,
                max_concurrent=1,
            )
        )

        response = await handle_inbound_run(request)

    assert response == "limit-response"
    provider_class.generate_validation_error_response.assert_called_once_with(
        TelephonyError.CONCURRENT_CALL_LIMIT
    )
    mock_db.create_workflow_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_smallwebrtc_run_reaching_telephony_websocket_closes_without_running():
    websocket = AsyncMock()
    workflow_run = SimpleNamespace(
        id=501,
        workflow_id=33,
        mode=WorkflowRunMode.SMALLWEBRTC.value,
        state=WorkflowRunState.INITIALIZED.value,
        initial_context={},
        gathered_context={},
    )
    workflow = SimpleNamespace(id=33, organization_id=11, user_id=99)
    provider_lookup = AsyncMock()

    with (
        patch("api.routes.telephony.db_client") as mock_db,
        patch("api.routes.telephony.call_concurrency") as mock_concurrency,
        # The rental-suspension gate reads the config's default caller ID.
        # These tests mock every dependency of the route so nothing touches a
        # real database; this is one more of them.
        patch(
            "api.routes.telephony.number_lifecycle.assert_configuration_may_serve",
            new=AsyncMock(return_value=None),
        ),
        # The provider-key gate resolves the account's model configuration
        # through its own clients, not the one patched above. Stubbed for the
        # same reason as the suspension gate; it is covered in
        # test_byok_key_readiness.py.
        patch(
            "api.routes.telephony.key_readiness.assert_workflow_may_run",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "api.routes.telephony.get_telephony_provider_for_run",
            new=provider_lookup,
        ),
    ):
        mock_concurrency.unregister_active_call = AsyncMock()
        mock_db.get_workflow_run = AsyncMock(return_value=workflow_run)
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.update_workflow_run = AsyncMock()

        await _handle_telephony_websocket(websocket, 33, 11, 501)

    mock_db.get_workflow_run.assert_awaited_once_with(501, organization_id=11)
    mock_db.get_workflow.assert_awaited_once_with(33, organization_id=11)
    websocket.close.assert_awaited_once_with(
        code=4400,
        reason=(
            "smallwebrtc runs connect through the WebRTC signaling endpoint, "
            "not the telephony websocket"
        ),
    )
    assert mock_db.update_workflow_run.await_count == 0
    assert provider_lookup.await_count == 0
    mock_concurrency.unregister_active_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_telephony_websocket_closes_when_state_changes_after_initial_read():
    """The initial ``state == INITIALIZED`` read can go stale across the
    awaits before the run is flipped to RUNNING (e.g. a terminal status
    callback landing in that window). The initialized -> running write must
    re-check the state atomically and refuse to start the pipeline if it has
    since moved on -- otherwise a picked-up call gets a media stream that
    never starts talking and immediately ends.
    """
    websocket = AsyncMock()
    workflow_run = SimpleNamespace(
        id=501,
        workflow_id=33,
        mode="phone",
        state=WorkflowRunState.INITIALIZED.value,
        initial_context={"provider": "twilio"},
        gathered_context={},
    )
    workflow = SimpleNamespace(id=33, organization_id=11, user_id=99)
    provider = SimpleNamespace(
        PROVIDER_NAME="twilio",
        handle_websocket=AsyncMock(),
    )

    with (
        patch("api.routes.telephony.db_client") as mock_db,
        patch(
            "api.routes.telephony.get_telephony_provider_for_run",
            new=AsyncMock(return_value=provider),
        ),
    ):
        mock_db.get_workflow_run = AsyncMock(return_value=workflow_run)
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.update_workflow_run = AsyncMock(
            side_effect=WorkflowRunStateConflictError(
                expected_state=WorkflowRunState.INITIALIZED.value,
                actual_state=WorkflowRunState.COMPLETED.value,
            )
        )

        await _handle_telephony_websocket(websocket, 33, 11, 501)

    mock_db.update_workflow_run.assert_awaited_once_with(
        run_id=501,
        state=WorkflowRunState.RUNNING.value,
        expected_state=WorkflowRunState.INITIALIZED.value,
    )
    websocket.close.assert_awaited_once_with(
        code=4409, reason="Workflow run not available for connection"
    )
    provider.handle_websocket.assert_not_awaited()


def test_a_verified_destination_is_dialled_outside_the_calling_window():
    """21:54 on your own handset is testing, not telemarketing.

    The TCCCPR window exists so a stranger is not rung at night. Applying it to
    a number the account has *proved it controls* protects nobody and makes the
    product untestable for half the working day — which is what it did.

    Note this test does not hold the window open: it lets the real
    ``within_calling_hours`` see a closed window, so the call only succeeds if
    the exemption reached the gate.
    """
    app = _make_test_app()
    client = TestClient(app)

    workflow = _workflow()
    provider = _provider()

    with (
        patch("api.routes.telephony.db_client") as mock_db,
        patch("api.routes.telephony.call_concurrency") as mock_concurrency,
        # The window is genuinely shut, as it was at 21:54 IST.
        patch(
            "api.services.compliance.dnd.within_calling_hours",
            lambda **kwargs: False,
        ),
        patch(
            "api.routes.telephony.number_lifecycle.assert_configuration_may_serve",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "api.routes.telephony.key_readiness.assert_workflow_may_run",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "api.routes.telephony.authorize_workflow_run_start",
            new=AsyncMock(
                return_value=SimpleNamespace(has_quota=True, error_message="")
            ),
        ),
        patch(
            "api.routes.telephony.get_default_telephony_provider",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "api.routes.telephony.get_backend_endpoints",
            new=AsyncMock(return_value=("https://api.example.com", "wss://ignored")),
        ),
    ):
        mock_concurrency.acquire_org_slot = AsyncMock(return_value=object())
        mock_concurrency.bind_workflow_run = AsyncMock()
        mock_concurrency.release_slot = AsyncMock()
        mock_concurrency.release_workflow_run_slot = AsyncMock()

        mock_db.get_user_configurations = AsyncMock(
            return_value=SimpleNamespace(test_phone_number=None)
        )
        mock_db.is_number_dnd_listed = AsyncMock(return_value=False)
        # The account verified this handset: a code went to it and came back.
        mock_db.get_verified_number = AsyncMock(
            return_value=SimpleNamespace(status="verified")
        )
        mock_db.get_default_telephony_configuration = AsyncMock(
            return_value=SimpleNamespace(id=55)
        )
        mock_db.get_telephony_configuration_for_org = AsyncMock(
            return_value=SimpleNamespace(id=55, is_platform_managed=False)
        )
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.create_workflow_run = AsyncMock(
            return_value=SimpleNamespace(
                id=502, name="WR-TEL-OUT-00000002", initial_context={}
            )
        )
        mock_db.update_workflow_run = AsyncMock()

        response = client.post(
            "/telephony/initiate-call",
            json={"workflow_id": workflow.id, "phone_number": "+919876543210"},
        )

    assert response.status_code == 200, response.text
    provider.initiate_call.assert_awaited_once()


def test_an_unverified_destination_still_obeys_the_calling_window():
    """The exemption is proof-gated. Without the proof, the window holds.

    This is the half that keeps the change honest: a number the account has
    not verified is somebody else's phone, and somebody else's phone does not
    ring at 21:54 because a test call asked it to.
    """
    app = _make_test_app()
    client = TestClient(app)

    workflow = _workflow()
    provider = _provider()

    with (
        patch("api.routes.telephony.db_client") as mock_db,
        patch("api.routes.telephony.call_concurrency") as mock_concurrency,
        patch(
            "api.services.compliance.dnd.within_calling_hours",
            lambda **kwargs: False,
        ),
        patch(
            "api.routes.telephony.number_lifecycle.assert_configuration_may_serve",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "api.routes.telephony.key_readiness.assert_workflow_may_run",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "api.routes.telephony.authorize_workflow_run_start",
            new=AsyncMock(
                return_value=SimpleNamespace(has_quota=True, error_message="")
            ),
        ),
        patch(
            "api.routes.telephony.get_default_telephony_provider",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "api.routes.telephony.get_backend_endpoints",
            new=AsyncMock(return_value=("https://api.example.com", "wss://ignored")),
        ),
    ):
        mock_concurrency.acquire_org_slot = AsyncMock(return_value=object())
        mock_concurrency.bind_workflow_run = AsyncMock()
        mock_concurrency.release_slot = AsyncMock()
        mock_concurrency.release_workflow_run_slot = AsyncMock()

        mock_db.get_user_configurations = AsyncMock(
            return_value=SimpleNamespace(test_phone_number=None)
        )
        mock_db.is_number_dnd_listed = AsyncMock(return_value=False)
        mock_db.get_verified_number = AsyncMock(return_value=None)
        mock_db.get_default_telephony_configuration = AsyncMock(
            return_value=SimpleNamespace(id=55)
        )
        mock_db.get_telephony_configuration_for_org = AsyncMock(
            return_value=SimpleNamespace(id=55, is_platform_managed=False)
        )
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.create_workflow_run = AsyncMock()
        mock_db.update_workflow_run = AsyncMock()

        response = client.post(
            "/telephony/initiate-call",
            json={"workflow_id": workflow.id, "phone_number": "+919876543210"},
        )

    assert response.status_code == 451
    provider.initiate_call.assert_not_awaited()
