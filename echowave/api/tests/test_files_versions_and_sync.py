"""Files behave like a synced vault: an upload of the same name is the next
version of the same file, each file says plainly whether it is Reading,
Ready or Couldn't read, and other clients can ask what changed since they
last looked.

Against the real database, and through the real ingestion task where the
point is what an agent reads afterwards.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from api.db import db_client
from api.db.models import (
    KnowledgeBaseDocumentModel,
    OrganizationModel,
    PaymentMandateModel,
    UserModel,
)
from api.enums import MandateStatus
from api.services.billing import mandates as mandate_service
from api.services.billing import subscription_plans
from api.services.knowledge_base import folders, sync, upload_keys, versions
from api.tasks import knowledge_base_processing as task_module
from api.tests.test_knowledge_base_end_to_end import (
    DeterministicEmbeddingService,
    _embed,
)


async def _organization(async_session, slug: str):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    user = UserModel(provider_id=f"user-{slug}", selected_organization_id=org.id)
    async_session.add(user)
    await async_session.flush()
    return org, user


async def _subscribed(async_session, org) -> None:
    await subscription_plans.ensure_seeded(async_session)
    async_session.add(
        PaymentMandateModel(
            organization_id=org.id,
            provider="razorpay",
            purpose=mandate_service.PURPOSE_STARTER_PLAN,
            subscription_id=f"sub_vs_{org.id}",
            plan_id="plan_starter",
            plan_code=subscription_plans.STARTER,
            status=MandateStatus.ACTIVE.value,
            price_paise=299900,
        )
    )
    await async_session.flush()


def _uuid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


@pytest.fixture
def queued(monkeypatch):
    jobs = []

    async def record(*args, **kwargs):
        jobs.append(args)

    monkeypatch.setattr("api.routes.knowledge_base.enqueue_job", record)
    return jobs


async def _upload(client, org, name: str, n: int, **extra):
    key = upload_keys.build_document_key(org.id, _uuid(n), name)
    response = await client.post(
        "/api/v1/knowledge-base/process-document",
        json={"document_uuid": _uuid(n), "s3_key": key, "scope": "org", **extra},
    )
    assert response.status_code == 200, response.text
    return response.json(), key


@pytest.mark.asyncio
class TestUploadingAgain:
    async def test_the_same_name_in_the_same_folder_is_the_next_version(
        self, db_session, async_session, test_client_factory, queued
    ):
        org, user = await _organization(async_session, "vs-again")
        await _subscribed(async_session, org)
        async with test_client_factory(user) as client:
            first, _ = await _upload(client, org, "Price list.pdf", 1)
            # The first read failed; the error must not outlive the next one.
            await db_client.update_document_status(
                first["id"], "failed", error_message="Could not open it"
            )
            second, second_key = await _upload(client, org, "price list.pdf", 2)

        assert second["document_uuid"] == first["document_uuid"]
        assert second["version"] == 2
        assert [v["version"] for v in second["versions"]] == [1, 2]
        assert second["versions"][-1]["current"] is True
        assert second["state"] == "reading"
        assert second["processing_error"] is None
        assert second["custom_metadata"]["s3_key"] == second_key
        # Read again, from the new upload, on the same row.
        assert queued[-1][1:3] == (first["id"], second_key)

        listed = await db_client.get_documents_for_organization(org.id)
        assert len(listed) == 1

    async def test_another_folder_or_another_agent_is_another_file(
        self, db_session, async_session, test_client_factory, queued
    ):
        org, user = await _organization(async_session, "vs-elsewhere")
        await _subscribed(async_session, org)
        folder = await folders.create(
            org.id, name="Old", parent_id=None, created_by=user.id
        )
        async with test_client_factory(user) as client:
            top, _ = await _upload(client, org, "rates.pdf", 11)
            inside, _ = await _upload(
                client, org, "rates.pdf", 12, file_folder_id=folder["id"]
            )
        assert inside["document_uuid"] != top["document_uuid"]
        assert inside["version"] == 1 and top["version"] == 1


@pytest.mark.asyncio
class TestState:
    def test_reading_ready_and_couldnt_read_with_the_reason(self):
        assert versions.state(
            processing_status="pending",
            processing_error=None,
            version=1,
            total_chunks=0,
        ) == ("reading", None)
        assert versions.state(
            processing_status="completed",
            processing_error=None,
            version=3,
            total_chunks=9,
        ) == ("ready", None)
        label, detail = versions.state(
            processing_status="failed",
            processing_error="This PDF is password protected.",
            version=2,
            total_chunks=5,
        )
        assert label == "failed"
        assert detail == (
            "This PDF is password protected. Agents still answer from version 1."
        )
        # Failed with no reason recorded still says something.
        assert versions.state(
            processing_status="failed", processing_error=None, version=1, total_chunks=0
        )[1]
        reading, meanwhile = versions.state(
            processing_status="processing",
            processing_error=None,
            version=4,
            total_chunks=2,
        )
        assert reading == "reading" and "version 3" in meanwhile


@pytest.fixture
def ingest(monkeypatch, tmp_path):
    """The ingestion task on local files and a deterministic embedder."""
    sources: dict[str, str] = {}

    async def fake_download(s3_key, destination):
        Path(destination).write_text(sources[s3_key], encoding="utf-8")
        return True

    async def no_head(s3_key):
        return None

    monkeypatch.setattr(
        task_module,
        "storage_fs",
        SimpleNamespace(adownload_file=fake_download, aget_file_metadata=no_head),
    )

    async def fake_build(**kwargs):
        return DeterministicEmbeddingService()

    monkeypatch.setattr(task_module, "build_embedding_service", fake_build)

    async def effective(**kwargs):
        return SimpleNamespace(
            embeddings=SimpleNamespace(
                provider="openai",
                api_key="sk-test",
                model="test-bag-of-words",
                base_url=None,
                endpoint=None,
                api_version=None,
                key_source="byok",
            )
        )

    monkeypatch.setattr(
        "api.services.configuration.ai_model_configuration."
        "get_effective_ai_model_configuration_for_workflow",
        effective,
    )
    monkeypatch.setattr(
        "api.services.configuration.ai_model_configuration."
        "apply_managed_embeddings_base_url",
        lambda **kwargs: None,
    )

    async def run(document_id, org, user, key, text):
        sources[key] = text
        await task_module.process_knowledge_base_document(
            ctx={},
            document_id=document_id,
            s3_key=key,
            organization_id=org.id,
            created_by_provider_id=str(user.provider_id),
        )

    return run


@pytest.mark.asyncio
class TestTheNewVersionIsWhatAgentsRead:
    async def test_a_re_uploaded_file_serves_its_new_content(
        self, db_session, async_session, ingest
    ):
        org, user = await _organization(async_session, "vs-serves")
        key1 = upload_keys.build_document_key(org.id, _uuid(21), "hours.txt")
        doc = await db_client.create_document(
            organization_id=org.id,
            created_by=user.id,
            filename="hours.txt",
            file_size_bytes=0,
            file_hash="",
            mime_type="text/plain",
            custom_metadata={"s3_key": key1},
            document_uuid=_uuid(21),
            retrieval_mode="chunked",
            scope="org",
        )
        await ingest(
            doc.id,
            org,
            user,
            key1,
            "The shop opens at nine in the morning on weekdays.",
        )

        key2 = upload_keys.build_document_key(org.id, _uuid(22), "hours.txt")
        row = await db_client.get_document_by_id(doc.id)
        await versions.begin(
            row,
            organization_id=org.id,
            s3_key=key2,
            user_id=user.id,
            retrieval_mode="chunked",
        )
        # Until the new version is read, agents still have the old one.
        assert doc.document_uuid in await db_client.scoped_document_uuids(
            org.id, workflow_id=None, folder_id=None
        )
        await ingest(
            doc.id,
            org,
            user,
            key2,
            "From October the shop opens at eleven in the morning on weekdays.",
        )

        hits = await db_client.search_similar_chunks(
            query_embedding=_embed("when does the shop open in the morning"),
            organization_id=org.id,
            limit=5,
        )
        texts = " ".join(h["chunk_text"] for h in hits)
        assert "eleven" in texts and "nine" not in texts
        stored = await db_client.get_document_by_id(doc.id)
        assert stored.processing_status == "completed"
        assert versions.current(stored) == 2


@pytest.mark.asyncio
class TestChangesSince:
    async def _three_files(self, org, user):
        docs = []
        for n in range(3):
            docs.append(
                await db_client.create_document(
                    organization_id=org.id,
                    created_by=user.id,
                    filename=f"f{n}.txt",
                    file_size_bytes=1,
                    file_hash="",
                    mime_type="text/plain",
                    scope="org",
                )
            )
        return docs

    async def test_everything_then_only_what_changed_including_deletions(
        self, db_session, async_session
    ):
        org, user = await _organization(async_session, "vs-sync")
        stranger, stranger_user = await _organization(async_session, "vs-sync-x")
        docs = await self._three_files(org, user)
        await self._three_files(stranger, stranger_user)
        folder = await folders.create(
            org.id, name="Sync", parent_id=None, created_by=user.id
        )
        later = datetime.now(UTC) + timedelta(minutes=1)

        first = await sync.changes(org.id, cursor=None, now=later)
        assert {f["document"].document_uuid for f in first["files"]} == {
            d.document_uuid for d in docs
        }
        assert [f["name"] for f in first["folders"]] == ["Sync"]
        assert first["has_more"] is False

        # Nothing has changed since: nothing to tell (folders at the cursor's
        # own instant are allowed to repeat, files are not).
        quiet = await sync.changes(org.id, cursor=first["cursor"], now=later)
        assert quiet["files"] == []

        # A rename, a move and a deletion, stamped after the cursor.
        stamp = later + timedelta(seconds=1)
        async with db_client.async_session() as session:
            await session.execute(
                update(KnowledgeBaseDocumentModel)
                .where(KnowledgeBaseDocumentModel.id == docs[0].id)
                .values(
                    filename="renamed.txt",
                    file_folder_id=folder["id"],
                    updated_at=stamp,
                )
            )
            await session.execute(
                update(KnowledgeBaseDocumentModel)
                .where(KnowledgeBaseDocumentModel.id == docs[1].id)
                .values(is_active=False, updated_at=stamp)
            )
            await session.commit()

        changed = await sync.changes(
            org.id, cursor=first["cursor"], now=stamp + timedelta(minutes=1)
        )
        by_uuid = {f["document"].document_uuid: f for f in changed["files"]}
        assert set(by_uuid) == {docs[0].document_uuid, docs[1].document_uuid}
        assert by_uuid[docs[0].document_uuid]["document"].filename == "renamed.txt"
        assert by_uuid[docs[0].document_uuid]["folder_path"] == "Sync"
        assert by_uuid[docs[1].document_uuid]["deleted"] is True

    async def test_pages_through_many_files_stamped_in_one_instant(
        self, db_session, async_session
    ):
        org, user = await _organization(async_session, "vs-page")
        docs = await self._three_files(org, user)
        same = datetime(2026, 1, 1, tzinfo=UTC)
        async with db_client.async_session() as session:
            await session.execute(
                update(KnowledgeBaseDocumentModel)
                .where(KnowledgeBaseDocumentModel.organization_id == org.id)
                .values(updated_at=same)
            )
            await session.commit()

        seen: list[str] = []
        cursor = None
        for _ in range(5):
            page = await sync.changes(org.id, cursor=cursor, limit=1)
            seen += [f["document"].document_uuid for f in page["files"]]
            cursor = page["cursor"]
            if not page["has_more"]:
                break
        assert sorted(seen) == sorted(d.document_uuid for d in docs)

    async def test_the_newest_seconds_wait_for_the_next_call(
        self, db_session, async_session
    ):
        org, user = await _organization(async_session, "vs-settle")
        await self._three_files(org, user)
        # Stamped "now": not yet safe to report.
        assert (await sync.changes(org.id, cursor=None))["files"] == []

    async def test_a_cursor_it_did_not_issue_is_refused(self):
        with pytest.raises(sync.BadCursor):
            sync.decode("yesterday|x")

    async def test_through_the_route(
        self, db_session, async_session, test_client_factory
    ):
        org, user = await _organization(async_session, "vs-route")
        await db_client.create_document(
            organization_id=org.id,
            created_by=user.id,
            filename="old.txt",
            file_size_bytes=1,
            file_hash="",
            mime_type="text/plain",
            scope="org",
        )
        async with db_client.async_session() as session:
            await session.execute(
                update(KnowledgeBaseDocumentModel)
                .where(KnowledgeBaseDocumentModel.organization_id == org.id)
                .values(updated_at=datetime(2026, 1, 1, tzinfo=UTC))
            )
            await session.commit()
        async with test_client_factory(user) as client:
            response = await client.get("/api/v1/knowledge-base/changes")
            assert response.status_code == 200, response.text
            body = response.json()
            assert [f["filename"] for f in body["files"]] == ["old.txt"]
            assert body["files"][0]["state"] in ("reading", "ready", "failed")
            again = await client.get(
                "/api/v1/knowledge-base/changes", params={"cursor": body["cursor"]}
            )
            assert again.json()["files"] == []
            bad = await client.get(
                "/api/v1/knowledge-base/changes", params={"cursor": "nonsense|x"}
            )
            assert bad.status_code == 422
