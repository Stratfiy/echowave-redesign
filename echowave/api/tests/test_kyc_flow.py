"""The customer KYC flow, end to end against a real database.

Storage is stubbed — these tests are about the flow's rules, not MinIO. What
they do care about is that an account can only ever see and change its own
documents, because these are incorporation and identity records.
Uses the ``db_session`` fixture, which patches ``db_client`` onto the test's
transaction — the service layer goes through ``db_client``, so without it the
rows these tests create would be invisible to the code under test.
"""

import pytest

from api.db import db_client
from api.db.models import OrganizationModel, UserModel
from api.enums import KycBusinessType, KycDocumentKind, KycStatus
from api.services.kyc import documents as document_store
from api.services.kyc import service as kyc_service
from api.services.kyc.state import KycTransitionError

PDF = b"%PDF-1.4 fake"


@pytest.fixture(autouse=True)
def verification_open(monkeypatch):
    """These tests exercise the flow, so they need the door open.

    The launch gate is a deployment switch, not behaviour under test — except
    in the one test below that asserts it holds.
    """
    monkeypatch.setattr(kyc_service, "MANAGED_TELEPHONY_ENABLED", True)


@pytest.fixture
def stub_storage(monkeypatch):
    """Record what would have been stored, without needing MinIO."""
    stored: dict[str, bytes] = {}
    deleted: list[str] = []

    async def _store(*, organization_id, kind, filename, content, content_type):
        document_store.validate_upload(
            content_type=content_type, size_bytes=len(content)
        )
        key = document_store.storage_key(
            organization_id=organization_id, kind=kind, filename=filename
        )
        stored[key] = content
        return key

    async def _delete(key):
        deleted.append(key)
        stored.pop(key, None)
        return True

    monkeypatch.setattr(document_store, "store_document", _store)
    monkeypatch.setattr(document_store, "delete_document", _delete)
    monkeypatch.setattr(kyc_service.document_store, "store_document", _store)
    monkeypatch.setattr(kyc_service.document_store, "delete_document", _delete)
    return {"stored": stored, "deleted": deleted}


async def _org(session, slug: str) -> tuple[int, int]:
    user = UserModel(provider_id=f"user-{slug}")
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add_all([user, org])
    await session.flush()
    return org.id, user.id


async def _company_with_docs(session, slug: str) -> int:
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
    return org_id


@pytest.mark.usefixtures("stub_storage")
class TestSubmissionFlow:
    async def test_a_new_account_starts_unverified_with_no_telephony(
        self, db_session, async_session
    ):
        org_id, _ = await _org(async_session, "fresh")
        view = await kyc_service.get_view(org_id)

        assert view.status == KycStatus.NOT_STARTED.value
        assert view.telephony_enabled is False
        assert view.can_submit is False
        # Nothing is required until they say what kind of business they are.
        assert view.required_documents == ()

    async def test_business_type_determines_what_is_required(
        self, db_session, async_session
    ):
        org_id, _ = await _org(async_session, "company")
        view = await kyc_service.set_business_details(
            organization_id=org_id,
            business_type=KycBusinessType.COMPANY.value,
            legal_name="Acme Pvt Ltd",
            gstin="29ABCDE1234F1Z5",
        )
        assert set(view.required_documents) == {
            "certificate_of_incorporation",
            "gst_certificate",
        }
        assert set(view.missing_documents) == set(view.required_documents)
        assert view.can_submit is False

    async def test_cannot_submit_until_every_document_is_present(
        self, db_session, async_session
    ):
        org_id, user_id = await _org(async_session, "partial")
        await kyc_service.set_business_details(
            organization_id=org_id,
            business_type=KycBusinessType.COMPANY.value,
            legal_name="Partial Pvt Ltd",
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

        with pytest.raises(ValueError, match="Still needed"):
            await kyc_service.submit(org_id)

    async def test_a_complete_submission_moves_to_review(
        self, db_session, async_session
    ):
        org_id = await _company_with_docs(async_session, "complete")
        view = await kyc_service.submit(org_id)

        assert view.status == KycStatus.SUBMITTED.value
        assert view.submitted_at is not None
        assert view.missing_documents == ()
        # Still no calling — we have not reviewed it and the carrier has not
        # seen it.
        assert view.telephony_enabled is False

    async def test_reuploading_a_kind_replaces_it(
        self, db_session, async_session, stub_storage
    ):
        """The common case is fixing a rejected scan. Two copies would leave a
        reviewer guessing which one counts."""
        org_id = await _company_with_docs(async_session, "replace")
        before = await kyc_service.get_view(org_id)

        await kyc_service.upload_document(
            organization_id=org_id,
            uploaded_by=None,
            kind=KycDocumentKind.GST_CERTIFICATE.value,
            filename="gst-v2.pdf",
            content=b"%PDF-1.4 better scan",
            content_type="application/pdf",
        )
        after = await kyc_service.get_view(org_id)

        assert len(after.documents) == len(before.documents) == 2
        gst = [d for d in after.documents if d["kind"] == "gst_certificate"]
        assert len(gst) == 1
        assert gst[0]["filename"] == "gst-v2.pdf"
        # And the superseded object is cleaned up rather than orphaned.
        assert stub_storage["deleted"]

    async def test_a_document_summary_never_exposes_the_storage_key(
        self, db_session, async_session
    ):
        """The key is an internal locator; publishing it invites someone to
        construct a bucket URL from it."""
        org_id = await _company_with_docs(async_session, "nokey")
        view = await kyc_service.get_view(org_id)
        for document in view.documents:
            assert "storage_key" not in document
            assert not any("kyc/" in str(v) for v in document.values())

    async def test_an_unknown_document_kind_is_refused(self, db_session, async_session):
        org_id, _ = await _org(async_session, "badkind")
        with pytest.raises(ValueError, match="Unknown document type"):
            await kyc_service.upload_document(
                organization_id=org_id,
                uploaded_by=None,
                kind="passport-photo",
                filename="x.pdf",
                content=PDF,
                content_type="application/pdf",
            )

    async def test_an_unknown_business_type_is_refused(self, db_session, async_session):
        org_id, _ = await _org(async_session, "badtype")
        with pytest.raises(ValueError, match="business type"):
            await kyc_service.set_business_details(
                organization_id=org_id,
                business_type="sole-trader-ish",
                legal_name="X",
                gstin=None,
            )


@pytest.mark.usefixtures("stub_storage")
class TestRecordsSurviveTheirSession:
    """Every KYC client method hands its record back to a caller that reads it
    after the session has closed.

    The ``db_session`` fixture keeps one session open for the whole test, which
    hides detachment entirely — in production ``commit()`` expires every
    attribute, and a record that was not fully reloaded afterwards raises
    ``DetachedInstanceError`` on the first column read. So rather than trusting
    the fixture, these assert directly that nothing is left unloaded.
    """

    #: What a caller actually touches on a returned record: every column, plus
    #: the documents collection. `organization` is deliberately excluded — it is
    #: never read off these, and requiring it would force a pointless join.
    @staticmethod
    def _assert_complete(record):
        from sqlalchemy import inspect

        state = inspect(record)
        needed = {c.key for c in state.mapper.column_attrs} | {"documents"}
        missing = needed & state.unloaded
        assert not missing, f"would lazy-load after the session closes: {missing}"

    async def test_a_created_record_is_fully_loaded(self, db_session, async_session):
        org_id, _ = await _org(async_session, "detach-create")
        self._assert_complete(await db_client.get_or_create_kyc(org_id))

    async def test_an_updated_record_is_fully_loaded(self, db_session, async_session):
        """The one that actually broke: refreshing only ``documents`` after a
        commit leaves every column expired."""
        org_id = await _company_with_docs(async_session, "detach-update")
        updated = await db_client.update_kyc(org_id, status=KycStatus.SUBMITTED.value)
        self._assert_complete(updated)
        # And the values are really there, not just marked loaded.
        assert updated.status == KycStatus.SUBMITTED.value
        assert len(updated.documents) == 2

    async def test_deleting_returns_a_key_not_a_dead_row(
        self, db_session, async_session
    ):
        """The caller needs the storage key to remove the object, and reading
        it off the deleted instance would raise."""
        org_id = await _company_with_docs(async_session, "detach-delete")
        document_id = (await kyc_service.get_view(org_id)).documents[0]["id"]

        key = await db_client.delete_document(document_id, organization_id=org_id)
        assert isinstance(key, str) and key


@pytest.mark.usefixtures("stub_storage")
class TestTenantIsolation:
    async def test_one_account_cannot_delete_another_accounts_document(
        self, db_session, async_session
    ):
        victim = await _company_with_docs(async_session, "victim")
        attacker, _ = await _org(async_session, "attacker")

        victim_view = await kyc_service.get_view(victim)
        target_id = victim_view.documents[0]["id"]

        # The attacker names a document id that is not theirs.
        await kyc_service.remove_document(
            organization_id=attacker, document_id=target_id
        )

        # The victim's documents are untouched.
        assert len((await kyc_service.get_view(victim)).documents) == 2

    async def test_documents_are_scoped_on_read(self, db_session, async_session):
        victim = await _company_with_docs(async_session, "victim2")
        attacker, _ = await _org(async_session, "attacker2")

        target_id = (await kyc_service.get_view(victim)).documents[0]["id"]

        assert (
            await db_client.get_document(target_id, organization_id=attacker)
        ) is None
        assert (
            await db_client.get_document(target_id, organization_id=victim)
        ) is not None


@pytest.mark.usefixtures("stub_storage")
class TestApprovedRecordsAreFrozen:
    async def test_documents_cannot_be_swapped_after_carrier_approval(
        self, db_session, async_session
    ):
        """Re-verification is the carrier moving the account back, not a
        customer quietly changing a certificate afterwards."""
        org_id = await _company_with_docs(async_session, "frozen")
        await kyc_service.submit(org_id)
        await db_client.update_kyc(org_id, status=KycStatus.CARRIER_APPROVED.value)

        with pytest.raises(KycTransitionError, match="locked"):
            await kyc_service.upload_document(
                organization_id=org_id,
                uploaded_by=None,
                kind=KycDocumentKind.GST_CERTIFICATE.value,
                filename="swapped.pdf",
                content=PDF,
                content_type="application/pdf",
            )

    async def test_documents_cannot_be_deleted_after_carrier_approval(
        self, db_session, async_session
    ):
        org_id = await _company_with_docs(async_session, "frozen2")
        await kyc_service.submit(org_id)
        await db_client.update_kyc(org_id, status=KycStatus.CARRIER_APPROVED.value)
        document_id = (await kyc_service.get_view(org_id)).documents[0]["id"]

        with pytest.raises(KycTransitionError, match="locked"):
            await kyc_service.remove_document(
                organization_id=org_id, document_id=document_id
            )

    async def test_telephony_turns_on_only_at_carrier_approval(
        self, db_session, async_session
    ):
        org_id = await _company_with_docs(async_session, "gate")
        await kyc_service.submit(org_id)

        # Our own sign-off is not enough.
        await db_client.update_kyc(org_id, status=KycStatus.FORWARDED.value)
        assert (await kyc_service.get_view(org_id)).telephony_enabled is False

        await db_client.update_kyc(org_id, status=KycStatus.CARRIER_APPROVED.value)
        assert (await kyc_service.get_view(org_id)).telephony_enabled is True


@pytest.mark.usefixtures("stub_storage")
class TestTheFlowIsClosedUntilTheCarrierIsReady:
    """Managed telephony depends on a reseller arrangement with the carrier.

    Until that exists there is nowhere to forward documents to, and accepting
    incorporation certificates and identity records anyway would take on DPDP
    custody of them in exchange for nothing. So the door is shut in the
    service, not only in the UI.
    """

    async def test_submitting_is_refused_while_the_flow_is_closed(
        self, db_session, async_session, monkeypatch
    ):
        monkeypatch.setattr(kyc_service, "MANAGED_TELEPHONY_ENABLED", False)
        org_id = await _company_with_docs(async_session, "closed")

        with pytest.raises(ValueError, match="not open yet"):
            await kyc_service.submit(org_id)

        # And nothing moved: the account is still where it was.
        assert (
            await kyc_service.get_view(org_id)
        ).status == KycStatus.NOT_STARTED.value

    async def test_the_refusal_says_what_is_happening(
        self, db_session, async_session, monkeypatch
    ):
        """ "Not open yet, we will tell you" is actionable. A generic failure
        would send the customer to support to hear the same thing."""
        monkeypatch.setattr(kyc_service, "MANAGED_TELEPHONY_ENABLED", False)
        org_id = await _company_with_docs(async_session, "closed-message")

        with pytest.raises(ValueError) as raised:
            await kyc_service.submit(org_id)

        assert "carrier" in str(raised.value)
