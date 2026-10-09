"""Folders on the Files page: create, rename, move, delete, and files in them.

Against the real database, because every rule here is a query: which folders
an organisation owns, what a folder holds, where a file sits. "Folder" means a
*file folder* throughout -- never a channel (``folders``).
"""

from __future__ import annotations

import pytest

from api.db import db_client
from api.db.models import OrganizationModel, PaymentMandateModel, UserModel
from api.enums import MandateStatus
from api.services.billing import mandates as mandate_service
from api.services.billing import subscription_plans
from api.services.knowledge_base import folders, upload_keys

MENU_DOC = "ffffffff-0000-4000-8000-000000000001"
OTHER_DOC = "eeeeeeee-0000-4000-8000-000000000002"


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
            subscription_id=f"sub_ff_{org.id}",
            plan_id="plan_starter",
            plan_code=subscription_plans.STARTER,
            status=MandateStatus.ACTIVE.value,
            price_paise=299900,
        )
    )
    await async_session.flush()


async def _file(org, user, name: str, *, file_folder_id=None, scope="org"):
    return await db_client.create_document(
        organization_id=org.id,
        created_by=user.id,
        filename=name,
        file_size_bytes=10,
        file_hash="",
        mime_type="text/plain",
        scope=scope,
        file_folder_id=file_folder_id,
    )


@pytest.mark.asyncio
class TestFolders:
    async def test_nest_list_and_name_the_path(self, db_session, async_session):
        org, user = await _organization(async_session, "ff-nest")
        pricing = await folders.create(
            org.id, name="Pricing", parent_id=None, created_by=user.id
        )
        year = await folders.create(
            org.id, name=" 2026 ", parent_id=pricing["id"], created_by=user.id
        )
        await _file(org, user, "rates.pdf", file_folder_id=year["id"])

        listed = {row["name"]: row for row in await folders.listing(org.id)}
        assert year["name"] == "2026"
        assert listed["2026"]["path"] == "Pricing/2026"
        assert listed["2026"]["file_count"] == 1
        assert listed["Pricing"]["folder_count"] == 1

    async def test_two_folders_of_one_name_in_one_place_are_refused(
        self, db_session, async_session
    ):
        org, user = await _organization(async_session, "ff-dupe")
        await folders.create(org.id, name="HR", parent_id=None, created_by=user.id)
        with pytest.raises(folders.FolderError) as refused:
            await folders.create(org.id, name="hr", parent_id=None, created_by=user.id)
        assert refused.value.status_code == 409

    async def test_rename_and_move_and_never_inside_itself(
        self, db_session, async_session
    ):
        org, user = await _organization(async_session, "ff-move")
        a = await folders.create(org.id, name="A", parent_id=None, created_by=user.id)
        b = await folders.create(
            org.id, name="B", parent_id=a["id"], created_by=user.id
        )
        c = await folders.create(org.id, name="C", parent_id=None, created_by=user.id)

        renamed = await folders.rename(org.id, c["id"], "Contracts")
        assert renamed["name"] == "Contracts"

        moved = await folders.move(org.id, c["id"], b["id"])
        assert moved["path"] == "A/B/Contracts"

        with pytest.raises(folders.FolderError):
            await folders.move(org.id, a["id"], moved["id"])
        with pytest.raises(folders.FolderError):
            await folders.move(org.id, a["id"], a["id"])

        top = await folders.move(org.id, moved["id"], None)
        assert top["parent_id"] is None and top["path"] == "Contracts"

    async def test_deleting_a_folder_that_holds_something_asks_first(
        self, db_session, async_session
    ):
        org, user = await _organization(async_session, "ff-ask")
        outer = await folders.create(
            org.id, name="Outer", parent_id=None, created_by=user.id
        )
        inner = await folders.create(
            org.id, name="Inner", parent_id=outer["id"], created_by=user.id
        )
        await _file(org, user, "a.txt", file_folder_id=outer["id"])
        await _file(org, user, "b.txt", file_folder_id=inner["id"])

        with pytest.raises(folders.FolderNotEmpty) as asked:
            await folders.delete(org.id, outer["id"], contents=None)
        assert asked.value.status_code == 409
        assert (asked.value.files, asked.value.folders) == (2, 1)
        # Nothing went anywhere while it was being asked.
        assert {row["name"] for row in await folders.listing(org.id)} == {
            "Outer",
            "Inner",
        }

    async def test_moving_contents_up_keeps_every_file(self, db_session, async_session):
        org, user = await _organization(async_session, "ff-up")
        parent = await folders.create(
            org.id, name="Parent", parent_id=None, created_by=user.id
        )
        doomed = await folders.create(
            org.id, name="Doomed", parent_id=parent["id"], created_by=user.id
        )
        # A neighbour of the same name as a folder about to move up.
        await folders.create(
            org.id, name="Kids", parent_id=parent["id"], created_by=user.id
        )
        kids = await folders.create(
            org.id, name="Kids", parent_id=doomed["id"], created_by=user.id
        )
        file = await _file(org, user, "keep.txt", file_folder_id=doomed["id"])
        await _file(org, user, "deeper.txt", file_folder_id=kids["id"])

        outcome = await folders.delete(
            org.id, doomed["id"], contents=folders.MOVE_TO_PARENT
        )
        assert outcome["files_moved"] == 1 and outcome["folders_moved"] == 1

        listed = {row["id"]: row for row in await folders.listing(org.id)}
        assert doomed["id"] not in listed
        assert listed[kids["id"]]["parent_id"] == parent["id"]
        assert listed[kids["id"]]["name"] == "Kids (2)"
        assert listed[kids["id"]]["file_count"] == 1
        moved = await db_client.get_document_by_uuid(file.document_uuid, org.id)
        assert moved.is_active and moved.file_folder_id == parent["id"]

    async def test_deleting_contents_removes_the_whole_subtree(
        self, db_session, async_session
    ):
        org, user = await _organization(async_session, "ff-gone")
        top = await folders.create(
            org.id, name="Old", parent_id=None, created_by=user.id
        )
        below = await folders.create(
            org.id, name="Older", parent_id=top["id"], created_by=user.id
        )
        a = await _file(org, user, "a.txt", file_folder_id=top["id"])
        b = await _file(org, user, "b.txt", file_folder_id=below["id"])
        kept = await _file(org, user, "kept.txt")

        outcome = await folders.delete(
            org.id, top["id"], contents=folders.DELETE_CONTENTS
        )
        assert outcome == {"files_deleted": 2, "folders_deleted": 2}
        assert await folders.listing(org.id) == []
        assert await db_client.get_document_by_uuid(a.document_uuid, org.id) is None
        assert await db_client.get_document_by_uuid(b.document_uuid, org.id) is None
        assert await db_client.get_document_by_uuid(kept.document_uuid, org.id)

    async def test_an_empty_folder_just_goes(self, db_session, async_session):
        org, user = await _organization(async_session, "ff-empty")
        empty = await folders.create(
            org.id, name="Empty", parent_id=None, created_by=user.id
        )
        assert (await folders.delete(org.id, empty["id"], contents=None))[
            "folders_deleted"
        ] == 1

    async def test_a_dropped_folder_path_is_made_once(self, db_session, async_session):
        org, user = await _organization(async_session, "ff-ensure")
        first = await folders.ensure_path(
            org.id, parent_id=None, segments=["Contracts", "2026"], created_by=user.id
        )
        again = await folders.ensure_path(
            org.id, parent_id=None, segments=["contracts", "2026"], created_by=user.id
        )
        assert first["path"] == "Contracts/2026"
        assert again["id"] == first["id"]
        assert len(await folders.listing(org.id)) == 2


@pytest.mark.asyncio
class TestFilesInFolders:
    async def test_rename_keeps_the_ending_and_move_puts_it_there(
        self, db_session, async_session
    ):
        org, user = await _organization(async_session, "ff-place")
        folder = await folders.create(
            org.id, name="Policies", parent_id=None, created_by=user.id
        )
        doc = await _file(org, user, "refunds.pdf")

        renamed = await folders.place_document(
            org.id, doc.document_uuid, filename="Refund policy"
        )
        assert renamed.filename == "Refund policy.pdf"
        assert renamed.file_folder_id is None

        moved = await folders.place_document(
            org.id, doc.document_uuid, file_folder_id=folder["id"], move=True
        )
        assert moved.file_folder_id == folder["id"]
        assert moved.filename == "Refund policy.pdf"

        with pytest.raises(folders.FolderError):
            await folders.place_document(
                org.id, doc.document_uuid, filename="Refund policy.txt"
            )

        back = await folders.place_document(
            org.id, doc.document_uuid, file_folder_id=None, move=True
        )
        assert back.file_folder_id is None

    async def test_moving_a_file_changes_nothing_about_who_reads_it(
        self, db_session, async_session
    ):
        org, user = await _organization(async_session, "ff-reads")
        folder = await folders.create(
            org.id, name="Deep", parent_id=None, created_by=user.id
        )
        doc = await _file(org, user, "faq.txt")
        await db_client.update_document_status(doc.id, "completed", total_chunks=1)
        await folders.place_document(
            org.id, doc.document_uuid, file_folder_id=folder["id"], move=True
        )
        readable = await db_client.scoped_document_uuids(
            org.id, workflow_id=None, folder_id=None
        )
        assert doc.document_uuid in readable


@pytest.mark.asyncio
class TestAnotherWorkspace:
    async def test_cannot_see_rename_move_delete_or_fill_a_folder(
        self, db_session, async_session
    ):
        owner, owner_user = await _organization(async_session, "ff-owner")
        stranger, stranger_user = await _organization(async_session, "ff-stranger")
        folder = await folders.create(
            owner.id, name="Private", parent_id=None, created_by=owner_user.id
        )
        theirs = await _file(stranger, stranger_user, "mine.txt")
        owners = await _file(owner, owner_user, "owners.txt")

        assert await folders.listing(stranger.id) == []
        for attempt in (
            folders.rename(stranger.id, folder["id"], "Taken"),
            folders.move(stranger.id, folder["id"], None),
            folders.delete(stranger.id, folder["id"], contents="delete"),
            folders.create(
                stranger.id, name="x", parent_id=folder["id"], created_by=None
            ),
            folders.place_document(
                stranger.id,
                theirs.document_uuid,
                file_folder_id=folder["id"],
                move=True,
            ),
            folders.place_document(stranger.id, owners.document_uuid, filename="x"),
        ):
            with pytest.raises(folders.FolderNotFound):
                await attempt

        assert (await folders.listing(owner.id))[0]["name"] == "Private"


@pytest.mark.asyncio
class TestThroughTheRoutes:
    async def test_folders_and_moves_over_http(
        self, db_session, async_session, test_client_factory, monkeypatch
    ):
        async def no_enqueue(*args, **kwargs):
            return None

        monkeypatch.setattr("api.routes.knowledge_base.enqueue_job", no_enqueue)
        org, user = await _organization(async_session, "ff-http")
        stranger, stranger_user = await _organization(async_session, "ff-http-x")
        await _subscribed(async_session, org)
        await _subscribed(async_session, stranger)

        async with test_client_factory(user) as client:
            made = await client.post(
                "/api/v1/knowledge-base/file-folders", json={"name": "Menus"}
            )
            assert made.status_code == 201, made.text
            folder_id = made.json()["id"]

            key = upload_keys.build_document_key(org.id, MENU_DOC, "menu.pdf")
            processed = await client.post(
                "/api/v1/knowledge-base/process-document",
                json={
                    "document_uuid": MENU_DOC,
                    "s3_key": key,
                    "scope": "org",
                    "file_folder_id": folder_id,
                },
            )
            assert processed.status_code == 200, processed.text
            assert processed.json()["file_folder_id"] == folder_id

            in_folder = await client.get(
                "/api/v1/knowledge-base/documents",
                params={"file_folder_id": folder_id},
            )
            assert [d["filename"] for d in in_folder.json()["documents"]] == [
                "menu.pdf"
            ]
            top = await client.get(
                "/api/v1/knowledge-base/documents", params={"top_level": True}
            )
            assert top.json()["documents"] == []

            asked = await client.delete(
                f"/api/v1/knowledge-base/file-folders/{folder_id}"
            )
            assert asked.status_code == 409
            assert "1 file" in asked.json()["detail"]

            moved = await client.patch(
                f"/api/v1/knowledge-base/documents/{processed.json()['document_uuid']}",
                json={"file_folder_id": None, "filename": "Lunch menu"},
            )
            assert moved.status_code == 200, moved.text
            assert moved.json()["file_folder_id"] is None
            assert moved.json()["filename"] == "Lunch menu.pdf"

            gone = await client.delete(
                f"/api/v1/knowledge-base/file-folders/{folder_id}"
            )
            assert gone.status_code == 200, gone.text

        async with test_client_factory(stranger_user) as client:
            other = await client.post(
                "/api/v1/knowledge-base/file-folders", json={"name": "Theirs"}
            )
            assert other.status_code == 201
        async with test_client_factory(user) as client:
            # Another workspace's folder is not a place to put a file.
            key = upload_keys.build_document_key(org.id, OTHER_DOC, "x.pdf")
            refused = await client.post(
                "/api/v1/knowledge-base/process-document",
                json={
                    "document_uuid": OTHER_DOC,
                    "s3_key": key,
                    "scope": "org",
                    "file_folder_id": other.json()["id"],
                },
            )
            assert refused.status_code == 404
            listed = await client.get("/api/v1/knowledge-base/file-folders")
            assert listed.json()["folders"] == []
