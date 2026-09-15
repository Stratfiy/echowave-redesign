"""Every inbound webhook proves who sent it before anything is processed.

One test pair per route this change guards: an unsigned POST is refused
with 401 and nothing downstream runs; a signed one is processed. The
routes that already verified (WhatsApp, Razorpay, KYC, inbound email,
triggers, Twilio, Plivo, Vobiz, Vonage, Telnyx events, /inbound/run) keep
their own tests; ``TestEveryCallbackRouteIsGuarded`` lists them all and
fails if a new callback route appears without a verifier in its body.
"""

from __future__ import annotations

import inspect
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.services.telephony import webhook_guard

RUN = SimpleNamespace(id=11, workflow_id=5, organization_id=7)
WORKFLOW = SimpleNamespace(id=5, organization_id=7)
CONTEXT = SimpleNamespace(
    transfer_id="t-1",
    call_sid="c-2",
    original_call_sid="c-1",
    conference_name="conf",
    workflow_run_id=11,
    conference_id=None,
)


def _provider(valid: bool):
    provider = SimpleNamespace()
    provider.verify_inbound_signature = AsyncMock(return_value=valid)
    provider.parse_status_callback = lambda data: {
        "call_id": "c-1",
        "status": "completed",
    }
    provider.transfer_event_handler = None
    provider.PROVIDER_NAME = "test"
    return provider


async def _post(path: str, *, json=None, data=None):
    from api.app import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(path, json=json, data=data)


@pytest.fixture
def run_and_workflow():
    with (
        patch.object(
            webhook_guard.db_client,
            "get_workflow_run_by_id",
            AsyncMock(return_value=RUN),
        ),
        patch.object(
            webhook_guard.db_client,
            "get_workflow_by_id",
            AsyncMock(return_value=WORKFLOW),
        ),
        patch(
            "api.services.telephony.providers.cloudonix.routes.db_client.get_workflow_run_by_id",
            AsyncMock(return_value=RUN),
        ),
        patch(
            "api.services.telephony.providers.cloudonix.routes.db_client.get_workflow_by_id",
            AsyncMock(return_value=WORKFLOW),
        ),
        patch(
            "api.services.telephony.providers.cloudonix.routes.db_client.get_workflow_run_by_call_id",
            AsyncMock(return_value=RUN),
        ),
    ):
        yield


@pytest.fixture
def transfer_context():
    manager = SimpleNamespace(
        get_transfer_context=AsyncMock(return_value=CONTEXT),
        publish_transfer_event=AsyncMock(),
        store_transfer_context=AsyncMock(),
    )
    with (
        patch.object(
            webhook_guard, "get_call_transfer_manager", AsyncMock(return_value=manager)
        ),
        patch(
            "api.services.telephony.providers.cloudonix.routes.get_call_transfer_manager",
            AsyncMock(return_value=manager),
        ),
        patch(
            "api.services.telephony.providers.telnyx.routes.get_call_transfer_manager",
            AsyncMock(return_value=manager),
        ),
        patch(
            "api.routes.telephony.get_call_transfer_manager",
            AsyncMock(return_value=manager),
        ),
    ):
        yield manager


def _with_provider(valid: bool):
    provider = _provider(valid)
    resolver = AsyncMock(return_value=provider)
    return (
        patch.object(webhook_guard, "get_telephony_provider_for_run", resolver),
        *(
            patch(
                f"api.services.telephony.providers.{name}.routes.get_telephony_provider_for_run",
                resolver,
            )
            for name in ("cloudonix", "plivo", "vobiz", "vonage", "telnyx")
        ),
    )


class TestCloudonixStatusCallback:
    async def test_unsigned_is_refused_and_nothing_is_processed(self, run_and_workflow):
        process = AsyncMock()
        with (
            ExitStackPatches(False),
            patch(
                "api.services.telephony.providers.cloudonix.routes._process_status_update",
                process,
            ),
        ):
            response = await _post(
                "/api/v1/telephony/cloudonix/status-callback/11",
                json={"status": "completed"},
            )
        assert response.status_code == 401
        process.assert_not_awaited()

    async def test_signed_is_processed(self, run_and_workflow):
        process = AsyncMock()
        with (
            ExitStackPatches(True),
            patch(
                "api.services.telephony.providers.cloudonix.routes._process_status_update",
                process,
            ),
        ):
            response = await _post(
                "/api/v1/telephony/cloudonix/status-callback/11",
                json={"status": "completed"},
            )
        assert response.status_code == 200
        process.assert_awaited_once()


class TestCloudonixCdr:
    async def test_unsigned_is_refused(self, run_and_workflow):
        process = AsyncMock()
        with (
            ExitStackPatches(False),
            patch(
                "api.services.telephony.providers.cloudonix.routes._process_status_update",
                process,
            ),
        ):
            response = await _post(
                "/api/v1/telephony/cloudonix/cdr",
                json={
                    "domain": "d",
                    "session": {"token": "c-1"},
                    "disposition": "ANSWER",
                    "duration": 12,
                },
            )
        assert response.status_code == 401
        process.assert_not_awaited()

    async def test_signed_is_processed(self, run_and_workflow):
        process = AsyncMock()
        with (
            ExitStackPatches(True),
            patch(
                "api.services.telephony.providers.cloudonix.routes._process_status_update",
                process,
            ),
        ):
            response = await _post(
                "/api/v1/telephony/cloudonix/cdr",
                json={
                    "domain": "d",
                    "session": {"token": "c-1"},
                    "disposition": "ANSWER",
                    "duration": 12,
                },
            )
        assert response.status_code == 200
        process.assert_awaited_once()


class TestCloudonixTransferResult:
    async def test_unsigned_is_refused(self, run_and_workflow, transfer_context):
        with ExitStackPatches(False):
            response = await _post(
                "/api/v1/telephony/cloudonix/transfer-result/t-1",
                json={"StatusCallbackEvent": "participant-join", "Session": "s"},
            )
        assert response.status_code == 401
        transfer_context.publish_transfer_event.assert_not_awaited()

    async def test_signed_publishes(self, run_and_workflow, transfer_context):
        with ExitStackPatches(True):
            response = await _post(
                "/api/v1/telephony/cloudonix/transfer-result/t-1",
                json={"StatusCallbackEvent": "participant-join", "Session": "s"},
            )
        assert response.status_code == 200
        transfer_context.publish_transfer_event.assert_awaited_once()


class TestTelnyxTransferResult:
    async def test_unsigned_is_refused(self, run_and_workflow, transfer_context):
        with ExitStackPatches(False):
            response = await _post(
                "/api/v1/telephony/telnyx/transfer-result/t-1",
                json={
                    "data": {
                        "event_type": "call.hangup",
                        "payload": {"call_control_id": "x"},
                    }
                },
            )
        assert response.status_code == 401
        transfer_context.publish_transfer_event.assert_not_awaited()

    async def test_signed_publishes(self, run_and_workflow, transfer_context):
        with ExitStackPatches(True):
            response = await _post(
                "/api/v1/telephony/telnyx/transfer-result/t-1",
                json={
                    "data": {
                        "event_type": "call.hangup",
                        "payload": {"call_control_id": "x"},
                    }
                },
            )
        assert response.status_code == 200
        transfer_context.publish_transfer_event.assert_awaited_once()


class TestGenericTransferResult:
    async def test_unsigned_is_refused(self, run_and_workflow, transfer_context):
        with ExitStackPatches(False):
            response = await _post(
                "/api/v1/telephony/transfer-result/t-1",
                data={"CallStatus": "no-answer", "CallSid": "c-2"},
            )
        assert response.status_code == 401
        transfer_context.publish_transfer_event.assert_not_awaited()

    async def test_signed_publishes(self, run_and_workflow, transfer_context):
        with ExitStackPatches(True):
            response = await _post(
                "/api/v1/telephony/transfer-result/t-1",
                data={"CallStatus": "no-answer", "CallSid": "c-2"},
            )
        assert response.status_code == 200
        transfer_context.publish_transfer_event.assert_awaited_once()


def _status_patches(module: str, process: AsyncMock):
    """The status processor, wherever the route imports it from."""
    return (
        patch(f"{module}._process_status_update", process, create=True),
        patch(
            "api.services.telephony.status_processor._process_status_update", process
        ),
    )


class TestPlivoStatusCallback:
    MODULE = "api.services.telephony.providers.plivo.routes"

    async def test_unsigned_is_refused(self, run_and_workflow):
        process = AsyncMock()
        with ExitStackPatches(False, *_status_patches(self.MODULE, process)):
            response = await _post(
                "/api/v1/telephony/plivo/hangup-callback/11",
                data={"CallUUID": "c-1", "Event": "Hangup"},
            )
        assert response.status_code == 401
        process.assert_not_awaited()

    async def test_signed_is_processed(self, run_and_workflow):
        process = AsyncMock()
        with ExitStackPatches(True, *_status_patches(self.MODULE, process)):
            response = await _post(
                "/api/v1/telephony/plivo/hangup-callback/11",
                data={"CallUUID": "c-1", "Event": "Hangup"},
            )
        assert response.status_code == 200
        process.assert_awaited_once()


class TestVobizStatusCallback:
    MODULE = "api.services.telephony.providers.vobiz.routes"

    async def test_unsigned_is_refused(self, run_and_workflow):
        process = AsyncMock()
        with ExitStackPatches(False, *_status_patches(self.MODULE, process)):
            response = await _post(
                "/api/v1/telephony/vobiz/hangup-callback/11",
                data={"CallUUID": "c-1", "Event": "Hangup"},
            )
        assert response.status_code == 401
        process.assert_not_awaited()

    async def test_signed_is_processed(self, run_and_workflow):
        process = AsyncMock()
        with ExitStackPatches(True, *_status_patches(self.MODULE, process)):
            response = await _post(
                "/api/v1/telephony/vobiz/hangup-callback/11",
                data={"CallUUID": "c-1", "Event": "Hangup"},
            )
        assert response.status_code == 200
        process.assert_awaited_once()


class TestVonageEvents:
    MODULE = "api.services.telephony.providers.vonage.routes"

    async def test_unsigned_is_refused(self, run_and_workflow):
        process = AsyncMock()
        with ExitStackPatches(False, *_status_patches(self.MODULE, process)):
            response = await _post(
                "/api/v1/telephony/vonage/events/11",
                json={"status": "completed", "uuid": "c-1"},
            )
        assert response.status_code == 401
        process.assert_not_awaited()

    async def test_signed_is_processed(self, run_and_workflow):
        process = AsyncMock()
        with ExitStackPatches(True, *_status_patches(self.MODULE, process)):
            response = await _post(
                "/api/v1/telephony/vonage/events/11",
                json={"status": "completed", "uuid": "c-1"},
            )
        assert response.status_code == 200
        process.assert_awaited_once()


class TestTelnyxEvents:
    MODULE = "api.services.telephony.providers.telnyx.routes"
    EVENT = {
        "data": {"event_type": "call.hangup", "payload": {"call_control_id": "c-1"}}
    }

    async def test_unsigned_is_refused(self, run_and_workflow):
        process = AsyncMock()
        with ExitStackPatches(False, *_status_patches(self.MODULE, process)):
            response = await _post(
                "/api/v1/telephony/telnyx/events/11", json=self.EVENT
            )
        assert response.status_code == 401
        process.assert_not_awaited()

    async def test_signed_is_processed(self, run_and_workflow):
        process = AsyncMock()
        with ExitStackPatches(True, *_status_patches(self.MODULE, process)):
            response = await _post(
                "/api/v1/telephony/telnyx/events/11", json=self.EVENT
            )
        assert response.status_code == 200
        process.assert_awaited_once()


class ExitStackPatches:
    """The provider patches, as one context manager."""

    def __init__(self, valid: bool, *extra):
        from contextlib import ExitStack

        self._stack = ExitStack()
        self._patches = (*_with_provider(valid), *extra)

    def __enter__(self):
        for p in self._patches:
            self._stack.enter_context(p)
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


class TestEveryCallbackRouteIsGuarded:
    """A new callback route without a verifier fails here, not in production."""

    VERIFIERS = (
        "verify_inbound_signature",
        "require_signature",
        "verify_signature",
        "compare_digest",
        "_verify_devops_secret",
        "signature=",
    )

    def _routes(self):
        import api.routes.kyc
        import api.routes.payments
        import api.routes.public_email
        import api.routes.public_triggers
        import api.routes.public_whatsapp
        import api.routes.telephony
        from api.services.telephony.providers.cloudonix import routes as cloudonix
        from api.services.telephony.providers.plivo import routes as plivo
        from api.services.telephony.providers.telnyx import routes as telnyx
        from api.services.telephony.providers.twilio import routes as twilio
        from api.services.telephony.providers.vobiz import routes as vobiz
        from api.services.telephony.providers.vonage import routes as vonage

        return [
            api.routes.kyc,
            api.routes.payments,
            api.routes.public_email,
            api.routes.public_triggers,
            api.routes.public_whatsapp,
            api.routes.telephony,
            cloudonix,
            plivo,
            telnyx,
            twilio,
            vobiz,
            vonage,
        ]

    def test_each_callback_handler_verifies_or_only_answers_xml(self):
        unguarded = []
        for module in self._routes():
            for name, fn in inspect.getmembers(module, inspect.iscoroutinefunction):
                src = inspect.getsource(fn)
                if "@router." not in src:
                    continue
                path_line = src.splitlines()[0]
                if not any(
                    k in path_line
                    for k in (
                        "callback",
                        "webhook",
                        "inbound",
                        "events",
                        "cdr",
                        "transfer-result",
                        "carrier",
                        "xml",
                        "twiml",
                        "ncco",
                    )
                ):
                    continue
                # Routes that only answer with static XML/NCCO for the carrier
                # to play (no writes) are allowed without a signature.
                if (
                    "verify" in src
                    or any(v in src for v in self.VERIFIERS)
                    or "_verified" in src
                ):
                    continue
                # Delegation to a helper that verifies (Plivo, Vonage).
                if re.search(r"await _\w*(handle|verif)\w*\(", src):
                    continue
                if (
                    "return Response(content=" in src
                    and "db_client" not in src
                    and "publish" not in src
                    and "_process" not in src
                ):
                    continue
                if "generate_error_response" in src or "generic_hangup_response" in src:
                    continue
                unguarded.append(f"{module.__name__}.{name}")
        assert unguarded == [], (
            f"callback routes without a signature check: {unguarded}"
        )
