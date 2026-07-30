"""Staff review of KYC, and the gate the review does *not* open.

The property these tests exist to protect is that our approval and the
carrier's approval are different things. Staff sign-off forwards an application;
only the licensee's verdict lets an account place calls. Everything else here —
queue ordering, rejection reasons, the superuser gate — is in service of that.
"""

import pytest

from api.db import db_client
from api.db.models import OrganizationModel, UserModel
from api.enums import KycBusinessType, KycDocumentKind, KycStatus
from api.services.kyc import documents as document_store
from api.services.kyc import service as kyc_service
from api.services.kyc.carrier import (
    CarrierNotConfigured,
    CarrierStatus,
    CarrierSubmission,
    CarrierVerdict,
    PlivoCarrier,
    get_carrier,
)
from api.services.kyc.state import KycTransitionError

PDF = b"%PDF-1.4 fake"


@pytest.fixture
def stub_storage(monkeypatch):
    """Record what would have been stored, without needing MinIO."""
    stored: dict[str, bytes] = {}

    async def _store(*, organization_id, kind, filename, content, content_type):
        key = document_store.storage_key(
            organization_id=organization_id, kind=kind, filename=filename
        )
        stored[key] = content
        return key

    async def _delete(key):
        stored.pop(key, None)
        return True

    monkeypatch.setattr(kyc_service.document_store, "store_document", _store)
    monkeypatch.setattr(kyc_service.document_store, "delete_document", _delete)
    return stored


async def _org(session, slug: str) -> tuple[int, int]:
    user = UserModel(provider_id=f"user-{slug}")
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add_all([user, org])
    await session.flush()
    return org.id, user.id


async def _staff(session, slug: str) -> UserModel:
    user = UserModel(provider_id=f"staff-{slug}", is_superuser=True)
    session.add(user)
    await session.flush()
    return user


async def _submitted(session, slug: str) -> tuple[int, int]:
    """An account that has supplied everything and is waiting on us."""
    org_id, user_id = await _org(session, slug)
    await kyc_service.set_business_details(
        organization_id=org_id,
        business_type=KycBusinessType.COMPANY.value,
        legal_name=f"{slug} Pvt Ltd",
        gstin="29ABCDE1234F1Z5",
    )
    for kind in (
        KycDocumentKind.CERTIFICATE_OF_INCORPORATION,
        KycDocumentKind.GST_CERTIFICATE,
    ):
        await kyc_service.upload_document(
            organization_id=org_id,
            uploaded_by=user_id,
            kind=kind.value,
            filename=f"{kind.value}.pdf",
            content=PDF,
            content_type="application/pdf",
        )
    await kyc_service.submit(org_id)
    return org_id, user_id


@pytest.mark.usefixtures("stub_storage")
class TestReviewQueue:
    async def test_the_queue_shows_what_is_waiting_on_anyone(
        self, db_session, async_session
    ):
        """Both sides of the wait, because a reviewer needs to see what is stuck
        at the carrier as well as what is stuck on their own desk."""
        org_id, _ = await _submitted(async_session, "queued")
        staff = await _staff(async_session, "q1")

        items = await kyc_service.list_review_queue()
        mine = [i for i in items if i.organization_id == org_id]
        assert len(mine) == 1
        assert mine[0].status == KycStatus.SUBMITTED.value
        assert mine[0].organization_name == "org-queued"
        assert mine[0].legal_name == "queued Pvt Ltd"
        assert {d["kind"] for d in mine[0].documents} == {
            "certificate_of_incorporation",
            "gst_certificate",
        }

        await kyc_service.approve_and_forward(
            organization_id=org_id, reviewer_id=staff.id
        )
        forwarded = [
            i
            for i in await kyc_service.list_review_queue()
            if i.organization_id == org_id
        ]
        assert forwarded[0].status == KycStatus.FORWARDED.value

    async def test_the_queue_is_oldest_first(self, db_session, async_session):
        """Newest-first would let an old submission starve behind a steady
        arrival rate."""
        first, _ = await _submitted(async_session, "older")
        second, _ = await _submitted(async_session, "newer")

        ids = [
            i.organization_id
            for i in await kyc_service.list_review_queue()
            if i.organization_id in {first, second}
        ]
        assert ids == [first, second]

    async def test_a_settled_account_is_not_in_the_queue(
        self, db_session, async_session
    ):
        org_id, _ = await _submitted(async_session, "settled")
        staff = await _staff(async_session, "q2")
        await kyc_service.reject(
            organization_id=org_id, reviewer_id=staff.id, reason="Scan is illegible"
        )

        assert not [
            i
            for i in await kyc_service.list_review_queue()
            if i.organization_id == org_id
        ]
        # But it is still reachable when a reviewer asks for it by name.
        by_status = await kyc_service.list_review_queue(
            statuses=[KycStatus.REJECTED.value]
        )
        assert org_id in {i.organization_id for i in by_status}

    async def test_the_queue_never_carries_a_storage_key(
        self, db_session, async_session
    ):
        org_id, _ = await _submitted(async_session, "nokey")
        items = [
            i
            for i in await kyc_service.list_review_queue()
            if i.organization_id == org_id
        ]
        for document in items[0].documents:
            assert "storage_key" not in document


@pytest.mark.usefixtures("stub_storage")
class TestReviewDecisions:
    async def test_claiming_marks_who_is_looking_at_it(self, db_session, async_session):
        org_id, _ = await _submitted(async_session, "claim")
        staff = await _staff(async_session, "d1")

        view = await kyc_service.claim_for_review(
            organization_id=org_id, reviewer_id=staff.id
        )
        assert view.status == KycStatus.UNDER_REVIEW.value

        record = await db_client.get_kyc(org_id)
        assert record.reviewed_by == staff.id

    async def test_a_rejection_needs_a_reason(self, db_session, async_session):
        """A rejection without one becomes a support conversation, which is the
        thing the queue exists to avoid."""
        org_id, _ = await _submitted(async_session, "noreason")
        staff = await _staff(async_session, "d2")

        with pytest.raises(ValueError, match="reason"):
            await kyc_service.reject(
                organization_id=org_id, reviewer_id=staff.id, reason="   "
            )

    async def test_a_rejected_account_can_fix_and_resubmit(
        self, db_session, async_session
    ):
        org_id, user_id = await _submitted(async_session, "resubmit")
        staff = await _staff(async_session, "d3")
        await kyc_service.reject(
            organization_id=org_id, reviewer_id=staff.id, reason="GST cert is cropped"
        )

        view = await kyc_service.get_view(org_id)
        assert view.status == KycStatus.REJECTED.value
        assert view.rejection_reason == "GST cert is cropped"

        # Rejected records are not frozen, so the customer can replace the scan.
        await kyc_service.upload_document(
            organization_id=org_id,
            uploaded_by=user_id,
            kind=KycDocumentKind.GST_CERTIFICATE.value,
            filename="gst-fixed.pdf",
            content=PDF,
            content_type="application/pdf",
        )
        assert (await kyc_service.submit(org_id)).status == KycStatus.SUBMITTED.value

    async def test_an_incomplete_application_cannot_be_forwarded(
        self, db_session, async_session
    ):
        """The carrier round-trip is the slow part; sending an incomplete pack
        wastes it."""
        org_id, user_id = await _org(async_session, "incomplete")
        await kyc_service.set_business_details(
            organization_id=org_id,
            business_type=KycBusinessType.COMPANY.value,
            legal_name="Half Pvt Ltd",
            gstin=None,
        )
        await kyc_service.upload_document(
            organization_id=org_id,
            uploaded_by=user_id,
            kind=KycDocumentKind.CERTIFICATE_OF_INCORPORATION.value,
            filename="coi.pdf",
            content=PDF,
            content_type="application/pdf",
        )
        staff = await _staff(async_session, "d4")

        with pytest.raises(ValueError, match="missing"):
            await kyc_service.approve_and_forward(
                organization_id=org_id, reviewer_id=staff.id
            )

    async def test_a_settled_account_cannot_be_forwarded_again(
        self, db_session, async_session
    ):
        org_id, _ = await _submitted(async_session, "twice")
        staff = await _staff(async_session, "d5")
        await kyc_service.approve_and_forward(
            organization_id=org_id, reviewer_id=staff.id
        )

        with pytest.raises(KycTransitionError):
            await kyc_service.approve_and_forward(
                organization_id=org_id, reviewer_id=staff.id
            )


@pytest.mark.usefixtures("stub_storage")
class TestOnlyTheCarrierOpensTelephony:
    async def test_our_approval_forwards_but_does_not_enable_calling(
        self, db_session, async_session
    ):
        """The whole point of the two-stage machine. Under the DoT directive the
        licensee verifies the end user; our sign-off is a pre-screen."""
        org_id, _ = await _submitted(async_session, "forward")
        staff = await _staff(async_session, "c1")

        view = await kyc_service.approve_and_forward(
            organization_id=org_id, reviewer_id=staff.id
        )
        assert view.status == KycStatus.FORWARDED.value
        assert view.telephony_enabled is False

        record = await db_client.get_kyc(org_id)
        assert record.forwarded_at is not None
        assert record.carrier_reference == f"manual:{org_id}"
        assert record.carrier_approved_at is None

    async def test_the_carrier_verdict_is_what_enables_calling(
        self, db_session, async_session
    ):
        org_id, _ = await _submitted(async_session, "approved")
        staff = await _staff(async_session, "c2")
        await kyc_service.approve_and_forward(
            organization_id=org_id, reviewer_id=staff.id
        )

        view = await kyc_service.record_carrier_verdict(
            organization_id=org_id,
            verdict=CarrierVerdict.APPROVED,
            raw_status="approved",
        )
        assert view.status == KycStatus.CARRIER_APPROVED.value
        assert view.telephony_enabled is True
        assert (await db_client.get_kyc(org_id)).carrier_approved_at is not None

    async def test_a_pending_verdict_changes_nothing_but_the_check_time(
        self, db_session, async_session
    ):
        """So a poll can run every fifteen minutes without moving anything."""
        org_id, _ = await _submitted(async_session, "pending")
        staff = await _staff(async_session, "c3")
        await kyc_service.approve_and_forward(
            organization_id=org_id, reviewer_id=staff.id
        )

        result = await kyc_service.record_carrier_verdict(
            organization_id=org_id,
            verdict=CarrierVerdict.PENDING,
            raw_status="in_review",
        )
        assert result is None

        record = await db_client.get_kyc(org_id)
        assert record.status == KycStatus.FORWARDED.value
        assert record.carrier_status == "in_review"
        assert record.carrier_checked_at is not None

    async def test_a_carrier_rejection_keeps_telephony_shut_and_says_why(
        self, db_session, async_session
    ):
        org_id, _ = await _submitted(async_session, "carrierno")
        staff = await _staff(async_session, "c4")
        await kyc_service.approve_and_forward(
            organization_id=org_id, reviewer_id=staff.id
        )

        view = await kyc_service.record_carrier_verdict(
            organization_id=org_id,
            verdict=CarrierVerdict.REJECTED,
            raw_status="rejected",
            rejection_reason="Signatory ID does not match the certificate",
        )
        assert view.status == KycStatus.CARRIER_REJECTED.value
        assert view.telephony_enabled is False
        assert "Signatory ID" in view.carrier_rejection_reason

    async def test_a_verdict_cannot_arrive_before_we_forwarded(
        self, db_session, async_session
    ):
        """Otherwise a stray call to the verdict endpoint would approve an
        account no licensee has seen."""
        org_id, _ = await _submitted(async_session, "premature")

        with pytest.raises(KycTransitionError):
            await kyc_service.record_carrier_verdict(
                organization_id=org_id, verdict=CarrierVerdict.APPROVED
            )
        assert (await kyc_service.get_view(org_id)).telephony_enabled is False


@pytest.mark.usefixtures("stub_storage")
class TestCarrierSeam:
    async def test_forwarding_records_the_carrier_that_took_it(
        self, db_session, async_session, monkeypatch
    ):
        org_id, _ = await _submitted(async_session, "namedcarrier")
        staff = await _staff(async_session, "s1")

        sent = {}

        class FakeCarrier:
            name = "fake"

            async def submit(self, **kwargs):
                sent.update(kwargs)
                return CarrierSubmission(reference="app-123")

            async def check(self, reference):
                return CarrierStatus(verdict=CarrierVerdict.PENDING)

        monkeypatch.setitem(
            kyc_service.get_carrier.__globals__["_CARRIERS"], "fake", FakeCarrier()
        )

        view = await kyc_service.approve_and_forward(
            organization_id=org_id, reviewer_id=staff.id, carrier_name="fake"
        )
        assert view.status == KycStatus.FORWARDED.value

        record = await db_client.get_kyc(org_id)
        assert record.carrier == "fake"
        assert record.carrier_reference == "app-123"
        # The carrier gets the details it needs, and no storage keys.
        assert sent["legal_name"] == "namedcarrier Pvt Ltd"
        assert all("storage_key" not in d for d in sent["documents"])

    async def test_an_unconfigured_carrier_fails_loudly_and_moves_nothing(
        self, db_session, async_session
    ):
        """A silent success here would mark an account forwarded that nobody
        ever received."""
        org_id, _ = await _submitted(async_session, "unconfigured")
        staff = await _staff(async_session, "s2")

        with pytest.raises(CarrierNotConfigured):
            await kyc_service.approve_and_forward(
                organization_id=org_id, reviewer_id=staff.id, carrier_name="plivo"
            )

        record = await db_client.get_kyc(org_id)
        assert record.status == KycStatus.SUBMITTED.value
        assert record.forwarded_at is None

    async def test_an_unknown_carrier_name_is_refused(self):
        with pytest.raises(CarrierNotConfigured):
            get_carrier("some-carrier-we-never-integrated")

    async def test_the_plivo_stub_never_reports_success(self):
        """It is a seam, not an implementation. If it ever silently returned a
        submission, accounts would sit forwarded to nowhere."""
        with pytest.raises(CarrierNotConfigured):
            await PlivoCarrier().submit(
                organization_id=1,
                business_type="company",
                legal_name="X",
                gstin=None,
                documents=[],
            )
        with pytest.raises(CarrierNotConfigured):
            await PlivoCarrier().check("app-1")


@pytest.mark.usefixtures("stub_storage")
class TestTheStaffGate:
    """The review router is cross-account by definition, so the gate is the
    only thing standing between a normal user and other companies' documents."""

    async def _client(self, user):
        from contextlib import asynccontextmanager

        from httpx import ASGITransport, AsyncClient

        from api.app import app
        from api.services.auth.depends import get_superuser

        @asynccontextmanager
        async def _ctx():
            async def _override():
                return user

            app.dependency_overrides[get_superuser] = _override
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    yield client
            finally:
                app.dependency_overrides.pop(get_superuser, None)

        return _ctx()

    async def test_the_queue_is_unreachable_without_the_gate(
        self, db_session, async_session
    ):
        from httpx import ASGITransport, AsyncClient

        from api.app import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/admin/kyc/queue")
        assert response.status_code in (401, 403)

    async def test_a_reviewer_sees_the_queue_over_http(self, db_session, async_session):
        org_id, _ = await _submitted(async_session, "http")
        staff = await _staff(async_session, "g1")

        async with await self._client(staff) as client:
            response = await client.get("/api/v1/admin/kyc/queue")

        assert response.status_code == 200
        body = response.json()
        assert org_id in {i["organization_id"] for i in body["items"]}

    async def test_approve_then_verdict_over_http(self, db_session, async_session):
        org_id, _ = await _submitted(async_session, "httpflow")
        staff = await _staff(async_session, "g2")

        async with await self._client(staff) as client:
            approved = await client.post(f"/api/v1/admin/kyc/{org_id}/approve", json={})
            assert approved.status_code == 200
            assert approved.json() == {
                "status": "forwarded",
                "telephony_enabled": False,
                "missing_documents": [],
            }

            verdict = await client.post(
                f"/api/v1/admin/kyc/{org_id}/carrier-verdict",
                json={"verdict": "approved"},
            )

        assert verdict.status_code == 200
        assert verdict.json()["telephony_enabled"] is True

    async def test_an_illegal_transition_is_a_conflict_not_a_crash(
        self, db_session, async_session
    ):
        org_id, _ = await _submitted(async_session, "conflict")
        staff = await _staff(async_session, "g3")

        async with await self._client(staff) as client:
            await client.post(f"/api/v1/admin/kyc/{org_id}/approve", json={})
            again = await client.post(f"/api/v1/admin/kyc/{org_id}/approve", json={})

        assert again.status_code == 409

    async def test_an_unknown_verdict_is_refused(self, db_session, async_session):
        org_id, _ = await _submitted(async_session, "badverdict")
        staff = await _staff(async_session, "g4")

        async with await self._client(staff) as client:
            response = await client.post(
                f"/api/v1/admin/kyc/{org_id}/carrier-verdict",
                json={"verdict": "looks-fine-to-me"},
            )

        assert response.status_code == 400
        assert (await kyc_service.get_view(org_id)).telephony_enabled is False

    async def test_documents_are_streamed_as_downloads_never_rendered(
        self, db_session, async_session, monkeypatch
    ):
        """These are customer-supplied files. Even with the upload allow-list,
        nothing here should ever render same-origin in a reviewer's browser."""
        org_id, _ = await _submitted(async_session, "stream")
        staff = await _staff(async_session, "g5")
        document_id = (await kyc_service.get_view(org_id)).documents[0]["id"]

        async def _read(key):
            return PDF

        monkeypatch.setattr("api.routes.kyc_admin.read_document", _read)

        async with await self._client(staff) as client:
            response = await client.get(f"/api/v1/admin/kyc/documents/{document_id}")

        assert response.status_code == 200
        assert response.content == PDF
        assert response.headers["content-disposition"].startswith("attachment")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'none'" in response.headers["content-security-policy"]

    async def test_a_missing_document_is_a_404(self, db_session, async_session):
        staff = await _staff(async_session, "g6")
        async with await self._client(staff) as client:
            response = await client.get("/api/v1/admin/kyc/documents/999999")
        assert response.status_code == 404
