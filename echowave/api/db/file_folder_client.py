"""Folders on the Files page (``file_folders``), and moving files between them.

Not channel folders: those are ``folders`` / ``FolderClient``. Every query
here is filtered by ``organization_id`` in SQL, and a folder that has been
deleted is never returned as a place to put something.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import func, select, update

from api.db.base_client import BaseDBClient
from api.db.models import FileFolderModel, KnowledgeBaseDocumentModel


class FileFolderClient(BaseDBClient):
    async def list_file_folders(self, organization_id: int) -> list[FileFolderModel]:
        """Every live folder of the organisation, flat; the caller nests them."""
        async with self.async_session() as session:
            rows = await session.execute(
                select(FileFolderModel)
                .where(
                    FileFolderModel.organization_id == organization_id,
                    FileFolderModel.deleted_at.is_(None),
                )
                .order_by(func.lower(FileFolderModel.name), FileFolderModel.id)
            )
            return list(rows.scalars().all())

    async def get_file_folder(
        self, folder_id: int, *, organization_id: int
    ) -> FileFolderModel | None:
        """One live folder of this organisation, or None."""
        async with self.async_session() as session:
            return (
                await session.execute(
                    select(FileFolderModel).where(
                        FileFolderModel.id == folder_id,
                        FileFolderModel.organization_id == organization_id,
                        FileFolderModel.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()

    async def create_file_folder(
        self,
        *,
        organization_id: int,
        name: str,
        parent_id: int | None,
        created_by: int | None,
    ) -> FileFolderModel:
        async with self.async_session() as session:
            folder = FileFolderModel(
                organization_id=organization_id,
                name=name,
                parent_id=parent_id,
                created_by=created_by,
            )
            session.add(folder)
            await session.commit()
            await session.refresh(folder)
            return folder

    async def update_file_folder(
        self, folder_id: int, *, organization_id: int, **values
    ) -> FileFolderModel | None:
        """Set ``name`` and/or ``parent_id``. Org-scoped; None when not found."""
        async with self.async_session() as session:
            folder = (
                await session.execute(
                    select(FileFolderModel).where(
                        FileFolderModel.id == folder_id,
                        FileFolderModel.organization_id == organization_id,
                        FileFolderModel.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if folder is None:
                return None
            for key, value in values.items():
                setattr(folder, key, value)
            await session.commit()
            await session.refresh(folder)
            return folder

    async def file_folder_counts(self, organization_id: int) -> dict[int, int]:
        """Live files directly in each folder, by folder id."""
        async with self.async_session() as session:
            rows = await session.execute(
                select(
                    KnowledgeBaseDocumentModel.file_folder_id,
                    func.count(KnowledgeBaseDocumentModel.id),
                )
                .where(
                    KnowledgeBaseDocumentModel.organization_id == organization_id,
                    KnowledgeBaseDocumentModel.is_active == True,
                    KnowledgeBaseDocumentModel.file_folder_id.is_not(None),
                )
                .group_by(KnowledgeBaseDocumentModel.file_folder_id)
            )
            return {int(folder_id): int(count) for folder_id, count in rows.all()}

    async def documents_in_file_folders(
        self, organization_id: int, folder_ids: Iterable[int]
    ) -> list[KnowledgeBaseDocumentModel]:
        """Live files sitting directly in any of ``folder_ids``."""
        ids = list(folder_ids)
        if not ids:
            return []
        async with self.async_session() as session:
            rows = await session.execute(
                select(KnowledgeBaseDocumentModel).where(
                    KnowledgeBaseDocumentModel.organization_id == organization_id,
                    KnowledgeBaseDocumentModel.is_active == True,
                    KnowledgeBaseDocumentModel.file_folder_id.in_(ids),
                )
            )
            return list(rows.scalars().all())

    async def move_documents_to_file_folder(
        self,
        organization_id: int,
        document_ids: Iterable[int],
        file_folder_id: int | None,
    ) -> int:
        """Put these files in ``file_folder_id`` (None: the top level)."""
        ids = list(document_ids)
        if not ids:
            return 0
        async with self.async_session() as session:
            result = await session.execute(
                update(KnowledgeBaseDocumentModel)
                .where(
                    KnowledgeBaseDocumentModel.organization_id == organization_id,
                    KnowledgeBaseDocumentModel.id.in_(ids),
                )
                .values(
                    file_folder_id=file_folder_id,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def archive_documents(
        self, organization_id: int, document_ids: Iterable[int]
    ) -> int:
        """Soft-delete these files, as deleting one from the list does."""
        ids = list(document_ids)
        if not ids:
            return 0
        now = datetime.now(UTC)
        async with self.async_session() as session:
            result = await session.execute(
                update(KnowledgeBaseDocumentModel)
                .where(
                    KnowledgeBaseDocumentModel.organization_id == organization_id,
                    KnowledgeBaseDocumentModel.id.in_(ids),
                )
                .values(is_active=False, archived_at=now, updated_at=now)
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def soft_delete_file_folders(
        self, organization_id: int, folder_ids: Iterable[int]
    ) -> int:
        ids = list(folder_ids)
        if not ids:
            return 0
        now = datetime.now(UTC)
        async with self.async_session() as session:
            result = await session.execute(
                update(FileFolderModel)
                .where(
                    FileFolderModel.organization_id == organization_id,
                    FileFolderModel.id.in_(ids),
                )
                .values(deleted_at=now, updated_at=now)
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def reparent_file_folders(
        self,
        organization_id: int,
        folder_ids: Iterable[int],
        parent_id: int | None,
    ) -> int:
        ids = list(folder_ids)
        if not ids:
            return 0
        async with self.async_session() as session:
            result = await session.execute(
                update(FileFolderModel)
                .where(
                    FileFolderModel.organization_id == organization_id,
                    FileFolderModel.id.in_(ids),
                )
                .values(parent_id=parent_id, updated_at=datetime.now(UTC))
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def file_folders_changed_since(
        self,
        organization_id: int,
        since: datetime | None,
        *,
        until: datetime | None = None,
    ) -> list[FileFolderModel]:
        """Folders created, renamed, moved or deleted at or after ``since``
        and no later than ``until``, deleted ones included, oldest change
        first. At, not only after: a folder sharing the cursor's instant is
        sent again rather than missed, and applying it twice changes nothing."""
        async with self.async_session() as session:
            query = select(FileFolderModel).where(
                FileFolderModel.organization_id == organization_id
            )
            if since is not None:
                query = query.where(FileFolderModel.updated_at >= since)
            if until is not None:
                query = query.where(FileFolderModel.updated_at <= until)
            rows = await session.execute(
                query.order_by(FileFolderModel.updated_at, FileFolderModel.id)
            )
            return list(rows.scalars().all())
