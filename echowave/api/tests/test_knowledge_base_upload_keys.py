"""Who may point ``/process-document`` at which object.

The upload is two requests with a browser PUT between them, so the second one
carries the storage key of the file that was just uploaded. That makes it a
client-supplied pointer into a bucket shared by every organization on the
deployment, and whatever is at that pointer gets chunked, embedded and stored
under the caller's organization_id — where the caller's agent reads it out on
request.

The prefix is ``knowledge_base/{organization_id}/{uuid}/{filename}``, which is
guessable in shape. The tests below are the ones that would have caught a
customer reading another customer's contracts down the phone.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.db.models import OrganizationModel, UserModel
from api.services.knowledge_base import upload_keys


async def _organization(
    async_session, slug: str
) -> tuple[OrganizationModel, UserModel]:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    user = UserModel(provider_id=f"user-{slug}", selected_organization_id=org.id)
    async_session.add(user)
    await async_session.flush()
    return org, user


DOC = "6f1a0b32-6bcb-4a0e-9f6e-1b7a2c9d4e50"
OTHER_DOC = "b2f9c1d4-3a56-4c8b-8f21-0d7e6a5c4b33"


class TestSafeFilename:
    def test_an_ordinary_name_is_left_alone(self):
        assert upload_keys.safe_filename("refund-policy.pdf") == "refund-policy.pdf"

    def test_a_traversal_attempt_keeps_only_the_name(self):
        assert upload_keys.safe_filename("../../../etc/passwd") == "passwd"

    def test_a_windows_path_is_treated_the_same_way(self):
        assert upload_keys.safe_filename(r"..\..\windows\system32\sam") == "sam"

    def test_separators_inside_a_name_cannot_split_the_key(self):
        assert "/" not in upload_keys.safe_filename("quarterly/report.pdf")

    def test_a_name_that_is_only_dots_is_replaced(self):
        """``..`` as a filename is a directory reference wearing a name."""
        assert upload_keys.safe_filename("..") == "document"

    def test_an_empty_name_still_produces_a_key(self):
        assert upload_keys.safe_filename("   ") == "document"


class TestKeyBelongsTo:
    def test_the_key_this_organization_was_given_is_accepted(self):
        key = upload_keys.build_document_key(7, DOC, "policy.pdf")
        assert upload_keys.key_belongs_to(7, DOC, key)

    def test_another_organizations_key_is_refused(self):
        """The whole point. Organization 7 asking to ingest organization 4's
        upload is the cross-tenant read this function exists to stop."""
        theirs = upload_keys.build_document_key(4, OTHER_DOC, "contract.pdf")
        assert not upload_keys.key_belongs_to(7, DOC, theirs)

    def test_a_key_for_a_different_document_of_the_same_organization_is_refused(self):
        """Still theirs, but not the document this request claims to be about —
        and accepting it would let one upload be ingested repeatedly under new
        document rows."""
        other = upload_keys.build_document_key(7, OTHER_DOC, "policy.pdf")
        assert not upload_keys.key_belongs_to(7, DOC, other)

    def test_traversal_out_of_the_prefix_is_refused(self):
        """A prefix check would pass this: it starts with the right thing and
        then leaves."""
        key = f"knowledge_base/7/{DOC}/../../4/{OTHER_DOC}/contract.pdf"
        assert not upload_keys.key_belongs_to(7, DOC, key)

    def test_a_neighbouring_organization_id_is_not_a_prefix_match(self):
        """Organization 1 must not match organization 12's keys."""
        theirs = upload_keys.build_document_key(12, DOC, "policy.pdf")
        assert not upload_keys.key_belongs_to(1, DOC, theirs)

    def test_a_directory_rather_than_an_object_is_refused(self):
        assert not upload_keys.key_belongs_to(7, DOC, f"knowledge_base/7/{DOC}/")

    def test_a_uuid_that_is_a_path_component_is_refused(self):
        """``document_uuid`` is interpolated into the key, so a caller who can
        put ``..`` there writes their own prefix."""
        assert not upload_keys.key_belongs_to(
            7, f"../4/{OTHER_DOC}", f"knowledge_base/7/../4/{OTHER_DOC}/contract.pdf"
        )

    def test_an_empty_key_is_refused(self):
        assert not upload_keys.key_belongs_to(7, DOC, "")


@pytest.mark.asyncio
class TestTheRouteEnforcesIt:
    """Through the endpoint, because a correct helper nobody called is what
    the code looked like before this."""

    @pytest.fixture
    def no_enqueue(self, monkeypatch):
        """Processing is not the subject; whether the request is accepted is."""
        enqueued = []

        async def record(*args, **kwargs):
            enqueued.append(args)

        monkeypatch.setattr("api.routes.knowledge_base.enqueue_job", record)
        return enqueued

    async def test_a_key_from_another_organization_is_rejected(
        self, db_session, async_session, test_client_factory, no_enqueue
    ):
        victim, _ = await _organization(async_session, "keys-victim")
        _, attacker_user = await _organization(async_session, "keys-attacker")

        victims_key = upload_keys.build_document_key(
            victim.id, OTHER_DOC, "contract.pdf"
        )

        async with test_client_factory(attacker_user) as client:
            response = await client.post(
                "/api/v1/knowledge-base/process-document",
                json={
                    "document_uuid": OTHER_DOC,
                    "s3_key": victims_key,
                    "retrieval_mode": "chunked",
                },
            )

        assert response.status_code == 403
        assert no_enqueue == [], "the document was queued for ingestion anyway"

    async def test_the_organizations_own_key_is_accepted(
        self, db_session, async_session, test_client_factory, no_enqueue
    ):
        """The other half: the rule has to let the real upload through, or the
        feature is closed rather than fixed."""
        org, user = await _organization(async_session, "keys-owner")
        own_key = upload_keys.build_document_key(org.id, DOC, "policy.pdf")

        async with test_client_factory(user) as client:
            response = await client.post(
                "/api/v1/knowledge-base/process-document",
                json={
                    "document_uuid": DOC,
                    "s3_key": own_key,
                    "retrieval_mode": "chunked",
                },
            )

        assert response.status_code == 200, response.text
        assert response.json()["filename"] == "policy.pdf"
        assert len(no_enqueue) == 1

    async def test_the_minted_key_is_one_the_check_accepts(
        self, db_session, async_session, test_client_factory, monkeypatch
    ):
        """The two endpoints agree. If /upload-url ever mints a key shape
        /process-document refuses, uploading breaks completely — so this walks
        the real round trip rather than trusting that they use the same
        function."""
        org, user = await _organization(async_session, "keys-round-trip")

        async def fake_presign(**kwargs):
            return "https://example.invalid/put"

        monkeypatch.setattr(
            "api.routes.knowledge_base.storage_fs",
            SimpleNamespace(aget_presigned_put_url=fake_presign),
        )

        async with test_client_factory(user) as client:
            minted = await client.post(
                "/api/v1/knowledge-base/upload-url",
                json={
                    "filename": "quarterly report.pdf",
                    "mime_type": "application/pdf",
                },
            )

        assert minted.status_code == 200, minted.text
        body = minted.json()
        assert upload_keys.key_belongs_to(org.id, body["document_uuid"], body["s3_key"])

    async def test_a_filename_with_a_path_in_it_cannot_place_the_object_elsewhere(
        self, db_session, async_session, test_client_factory, monkeypatch
    ):
        """The name comes from a file picker, which is to say from whoever is
        driving the browser."""
        org, user = await _organization(async_session, "keys-traversal")
        signed_for = {}

        async def fake_presign(file_path, **kwargs):
            signed_for["key"] = file_path
            return "https://example.invalid/put"

        monkeypatch.setattr(
            "api.routes.knowledge_base.storage_fs",
            SimpleNamespace(aget_presigned_put_url=fake_presign),
        )

        async with test_client_factory(user) as client:
            minted = await client.post(
                "/api/v1/knowledge-base/upload-url",
                json={
                    "filename": "../../../campaigns/9/leads.csv",
                    "mime_type": "text/csv",
                },
            )

        assert minted.status_code == 200, minted.text
        assert signed_for["key"].startswith(f"knowledge_base/{org.id}/")
        assert ".." not in signed_for["key"]
