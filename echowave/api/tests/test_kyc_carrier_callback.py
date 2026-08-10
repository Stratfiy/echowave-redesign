"""The compliance callback: authentication first, idempotence second.

This endpoint can open telephony for an account, which makes it the highest-
value unauthenticated surface in the codebase. Two things are therefore tested
harder than anything else here: that an unsigned request changes nothing, and
that a redelivered callback is harmless.

Plivo redelivers on any non-2xx and can redeliver after a timeout it decided
about *after* we committed, so duplicate delivery is normal traffic rather than
an edge case.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from api.db import db_client
from api.db.models import OrganizationModel
from api.enums import KycStatus
from api.services.kyc import callback as kyc_callback
from api.services.kyc import service as kyc_service
from api.services.kyc.carrier import CarrierVerdict

TOKEN = "test-auth-token"
URL = "https://app.decibyl.ai/api/v1/kyc/carrier-callback"


def _sign(url: str, nonce: str, token: str = TOKEN, params: dict | None = None) -> str:
    from api.services.telephony.providers.plivo.provider import PlivoProvider

    payload = f"{PlivoProvider._construct_post_url(url, params or {})}.{nonce}"
    return base64.b64encode(
        hmac.new(token.encode(), payload.encode(), hashlib.sha256).digest()
    ).decode()


class TestSignature:
    def test_a_correctly_signed_callback_verifies(self):
        nonce = "12345"
        assert kyc_callback.verify_signature(
            url=URL,
            params={},
            signature=_sign(URL, nonce),
            nonce=nonce,
            auth_token=TOKEN,
        )

    def test_a_forged_signature_is_refused(self):
        assert not kyc_callback.verify_signature(
            url=URL,
            params={},
            signature="not-a-real-signature",
            nonce="12345",
            auth_token=TOKEN,
        )

    def test_a_signature_from_the_wrong_token_is_refused(self):
        nonce = "12345"
        assert not kyc_callback.verify_signature(
            url=URL,
            params={},
            signature=_sign(URL, nonce, token="someone-elses-token"),
            nonce=nonce,
            auth_token=TOKEN,
        )

    def test_a_signature_for_a_different_url_is_refused(self):
        """Otherwise a signature captured from any endpoint replays here."""
        nonce = "12345"
        assert not kyc_callback.verify_signature(
            url=URL,
            params={},
            signature=_sign("https://app.decibyl.ai/api/v1/other", nonce),
            nonce=nonce,
            auth_token=TOKEN,
        )

    def test_a_missing_signature_is_refused(self):
        assert not kyc_callback.verify_signature(
            url=URL, params={}, signature=None, nonce="12345", auth_token=TOKEN
        )

    def test_a_missing_nonce_is_refused(self):
        assert not kyc_callback.verify_signature(
            url=URL, params={}, signature=_sign(URL, "1"), nonce=None, auth_token=TOKEN
        )

    def test_no_configured_token_refuses_rather_than_accepts(self):
        """The dangerous default. An unset token must not mean "skip the check"."""
        assert not kyc_callback.verify_signature(
            url=URL,
            params={},
            signature=_sign(URL, "1"),
            nonce="1",
            auth_token=None,
        )

    def test_multiple_comma_separated_signatures_are_each_tried(self):
        nonce = "12345"
        header = f"bogus,{_sign(URL, nonce)}"
        assert kyc_callback.verify_signature(
            url=URL, params={}, signature=header, nonce=nonce, auth_token=TOKEN
        )


class TestPayloadExtraction:
    def test_compliance_id_is_read_from_any_documented_key(self):
        assert kyc_callback.extract_compliance_id({"compliance_id": "a"}) == "a"
        assert (
            kyc_callback.extract_compliance_id({"compliance_application_id": "b"})
            == "b"
        )

    def test_a_payload_without_an_id_yields_none(self):
        assert kyc_callback.extract_compliance_id({"status": "accepted"}) is None

    def test_reason_is_truncated_not_unbounded(self):
        reason = kyc_callback.extract_reason({"rejection_reason": "x" * 5000})
        assert len(reason) == 2000


async def _org(session, slug: str) -> int:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org.id


async def _forwarded(session, slug: str, reference: str) -> int:
    """An account sitting with the carrier, ready for a verdict."""
    org_id = await _org(session, slug)
    await db_client.get_or_create_kyc(org_id)
    await db_client.update_kyc(
        org_id,
        status=KycStatus.FORWARDED.value,
        carrier="plivo",
        carrier_reference=reference,
    )
    return org_id


class TestHandling:
    async def test_an_acceptance_opens_telephony(self, db_session, async_session):
        org_id = await _forwarded(async_session, "cb-ok", "app-ok")

        outcome = await kyc_callback.handle(
            {"compliance_id": "app-ok", "status": "accepted"}
        )
        assert outcome.handled
        assert outcome.organization_id == org_id

        record = await db_client.get_kyc(org_id)
        assert record.status == KycStatus.CARRIER_APPROVED.value

    async def test_a_repeat_delivery_is_a_no_op_not_an_error(
        self, db_session, async_session
    ):
        """Plivo redelivers. A second acceptance must not raise, because a
        non-2xx earns another redelivery of something we handled correctly."""
        org_id = await _forwarded(async_session, "cb-dup", "app-dup")
        payload = {"compliance_id": "app-dup", "status": "accepted"}

        first = await kyc_callback.handle(payload)
        second = await kyc_callback.handle(payload)

        assert first.handled
        assert second.handled  # accepted, and changed nothing
        record = await db_client.get_kyc(org_id)
        assert record.status == KycStatus.CARRIER_APPROVED.value

    async def test_a_suspension_after_approval_closes_the_gate(
        self, db_session, async_session
    ):
        org_id = await _forwarded(async_session, "cb-susp", "app-susp")
        await kyc_callback.handle({"compliance_id": "app-susp", "status": "accepted"})

        await kyc_callback.handle(
            {
                "compliance_id": "app-susp",
                "status": "suspended",
                "reason": "GST certificate lapsed",
            }
        )

        record = await db_client.get_kyc(org_id)
        assert record.status == KycStatus.SUSPENDED.value
        assert record.carrier_rejection_reason == "GST certificate lapsed"

        with pytest.raises(kyc_service.TelephonyNotVerified):
            await kyc_service.assert_may_place_calls(org_id)

    async def test_a_reinstatement_reopens_the_gate(
        self, db_session, async_session
    ):
        org_id = await _forwarded(async_session, "cb-rein", "app-rein")
        await kyc_callback.handle({"compliance_id": "app-rein", "status": "accepted"})
        await kyc_callback.handle({"compliance_id": "app-rein", "status": "suspended"})
        await kyc_callback.handle({"compliance_id": "app-rein", "status": "accepted"})

        record = await db_client.get_kyc(org_id)
        assert record.status == KycStatus.CARRIER_APPROVED.value
        await kyc_service.assert_may_place_calls(org_id)  # does not raise

    async def test_an_expiry_after_approval_is_recorded(
        self, db_session, async_session
    ):
        org_id = await _forwarded(async_session, "cb-exp", "app-exp")
        await kyc_callback.handle({"compliance_id": "app-exp", "status": "accepted"})
        await kyc_callback.handle({"compliance_id": "app-exp", "status": "expired"})

        record = await db_client.get_kyc(org_id)
        assert record.status == KycStatus.EXPIRED.value

    async def test_a_callback_for_an_unknown_application_changes_nothing(
        self, db_session, async_session
    ):
        outcome = await kyc_callback.handle(
            {"compliance_id": "never-filed", "status": "accepted"}
        )
        assert not outcome.handled
        assert "No account" in outcome.detail

    async def test_an_unrecognised_status_is_refused_not_guessed(
        self, db_session, async_session
    ):
        org_id = await _forwarded(async_session, "cb-weird", "app-weird")

        outcome = await kyc_callback.handle(
            {"compliance_id": "app-weird", "status": "escalated_to_legal"}
        )
        assert not outcome.handled

        record = await db_client.get_kyc(org_id)
        assert record.status == KycStatus.FORWARDED.value

    async def test_a_stale_verdict_that_does_not_apply_is_absorbed(
        self, db_session, async_session
    ):
        """Plivo does not guarantee ordering, so an out-of-order callback is
        expected traffic rather than a fault."""
        org_id = await _forwarded(async_session, "cb-stale", "app-stale")
        await kyc_callback.handle({"compliance_id": "app-stale", "status": "accepted"})

        # A rejection cannot follow an acceptance: from approved the only ways
        # out are suspension and expiry.
        outcome = await kyc_callback.handle(
            {"compliance_id": "app-stale", "status": "rejected"}
        )
        assert not outcome.handled

        record = await db_client.get_kyc(org_id)
        assert record.status == KycStatus.CARRIER_APPROVED.value

    async def test_an_application_can_expire_while_still_under_review(
        self, db_session, async_session
    ):
        """Documents age out during review, and Plivo says so on the callback.
        Discarding it would leave the account waiting on a verdict for ever."""
        org_id = await _forwarded(async_session, "cb-lapse", "app-lapse")

        outcome = await kyc_callback.handle(
            {"compliance_id": "app-lapse", "status": "expired"}
        )
        assert outcome.handled

        record = await db_client.get_kyc(org_id)
        assert record.status == KycStatus.EXPIRED.value

    async def test_a_pending_status_only_records_that_we_heard(
        self, db_session, async_session
    ):
        org_id = await _forwarded(async_session, "cb-pending", "app-pending")

        outcome = await kyc_callback.handle(
            {"compliance_id": "app-pending", "status": "submitted"}
        )
        assert outcome.handled

        record = await db_client.get_kyc(org_id)
        assert record.status == KycStatus.FORWARDED.value
        assert record.carrier_checked_at is not None


class TestOverHttp:
    async def _post(self, payload, headers=None):
        from httpx import ASGITransport, AsyncClient

        from api.app import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post(
                "/api/v1/kyc/carrier-callback", json=payload, headers=headers or {}
            )

    async def test_an_unsigned_request_is_refused_with_403(
        self, db_session, async_session
    ):
        response = await self._post(
            {"compliance_id": "app-x", "status": "accepted"}
        )
        assert response.status_code == 403

    async def test_an_unsigned_request_changes_nothing(
        self, db_session, async_session
    ):
        """The property that matters more than the status code."""
        org_id = await _forwarded(async_session, "cb-http", "app-http")

        await self._post({"compliance_id": "app-http", "status": "accepted"})

        record = await db_client.get_kyc(org_id)
        assert record.status == KycStatus.FORWARDED.value

    async def test_a_signed_request_is_accepted_and_applied(
        self, db_session, async_session, monkeypatch
    ):
        org_id = await _forwarded(async_session, "cb-signed", "app-signed")

        # The route reads the platform token through the callback module.
        monkeypatch.setattr(kyc_callback, "PLATFORM_PLIVO_AUTH_TOKEN", TOKEN)

        nonce = "9876"
        url = "http://test/api/v1/kyc/carrier-callback"
        response = await self._post(
            {"compliance_id": "app-signed", "status": "accepted"},
            headers={
                "X-Plivo-Signature-V3": _sign(url, nonce),
                "X-Plivo-Signature-V3-Nonce": nonce,
            },
        )

        assert response.status_code == 200
        record = await db_client.get_kyc(org_id)
        assert record.status == KycStatus.CARRIER_APPROVED.value


def test_verdict_mapping_is_total_over_carrier_verdicts():
    """Every verdict a carrier can return has a status, except PENDING which
    deliberately maps to no transition at all."""
    mapping = kyc_service._VERDICT_TO_STATUS
    for verdict in CarrierVerdict:
        if verdict is CarrierVerdict.PENDING:
            assert verdict not in mapping
        else:
            assert verdict in mapping, verdict
