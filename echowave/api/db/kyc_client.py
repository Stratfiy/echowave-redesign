"""Data access for telephony KYC.

Every read and write is scoped to an organization. KYC records carry
incorporation and identity documents, so an unscoped lookup here would be a
tenant-isolation failure with real-world consequences, not just a leak of
metadata.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.db.base_client import BaseDBClient
from api.db.models import KycDocumentModel, OrganizationKycModel, OrganizationModel
from api.enums import KycStatus


def _loaded(organization_id: int):
    """A select that leaves nothing for the caller to lazy-load.

    Every method here returns a record that outlives its session, and callers
    read both columns and ``documents`` off it. ``commit()`` expires *all*
    attributes, so a post-commit ``refresh(attribute_names=["documents"])``
    loads the relationship but leaves the columns expired — the instance then
    detaches on session exit and the first column read raises
    ``DetachedInstanceError``. Re-selecting through this is what makes the
    returned record complete.
    """
    return (
        select(OrganizationKycModel)
        .options(selectinload(OrganizationKycModel.documents))
        .where(OrganizationKycModel.organization_id == organization_id)
        .execution_options(populate_existing=True)
    )


class KycClient(BaseDBClient):
    async def get_kyc(self, organization_id: int) -> OrganizationKycModel | None:
        """This account's KYC record with its documents, or None.

        ``populate_existing`` because this must always reflect the database.
        SQLAlchemy will not overwrite an already-loaded collection otherwise,
        so a caller that reads, mutates, then reads again through a shared
        session would be handed the pre-mutation document list.
        """
        async with self.async_session() as session:
            return await session.scalar(_loaded(organization_id))

    async def get_or_create_kyc(self, organization_id: int) -> OrganizationKycModel:
        """The record for this account, created in ``not_started`` if absent.

        Created lazily rather than on organization signup, because most
        accounts never ask for a phone number and an empty row for each of them
        is noise in the review queue's index.
        """
        async with self.async_session() as session:
            existing = await session.scalar(_loaded(organization_id))
            if existing is not None:
                return existing

            session.add(
                OrganizationKycModel(
                    organization_id=organization_id,
                    status=KycStatus.NOT_STARTED.value,
                )
            )
            await session.commit()
            return await session.scalar(_loaded(organization_id))

    async def update_kyc(
        self, organization_id: int, **fields
    ) -> OrganizationKycModel | None:
        """Apply field updates to this account's record.

        Callers pass an already-validated status; the state machine in
        ``services/kyc/state.py`` decides legality, not this client.
        """
        async with self.async_session() as session:
            record = await session.scalar(
                select(OrganizationKycModel).where(
                    OrganizationKycModel.organization_id == organization_id
                )
            )
            if record is None:
                return None

            for key, value in fields.items():
                if hasattr(record, key):
                    setattr(record, key, value)
            record.updated_at = datetime.now(UTC)

            await session.commit()
            return await session.scalar(_loaded(organization_id))

    async def add_document(
        self,
        *,
        organization_id: int,
        organization_kyc_id: int,
        kind: str,
        storage_key: str,
        filename: str,
        content_type: str | None,
        size_bytes: int,
        uploaded_by: int | None,
    ) -> KycDocumentModel:
        """Record an uploaded document.

        Returns the new row. The caller is responsible for having stored the
        bytes first — a row pointing at a key that was never written would show
        a reviewer a document that cannot be opened.
        """
        async with self.async_session() as session:
            document = KycDocumentModel(
                organization_id=organization_id,
                organization_kyc_id=organization_kyc_id,
                kind=kind,
                storage_key=storage_key,
                filename=filename,
                content_type=content_type,
                size_bytes=size_bytes,
                uploaded_by=uploaded_by,
            )
            session.add(document)
            await session.commit()
            await session.refresh(document)
            return document

    async def get_document(
        self, document_id: int, *, organization_id: int | None = None
    ) -> KycDocumentModel | None:
        """One document.

        ``organization_id`` is optional only so staff review can read across
        accounts; every customer-facing caller must pass it.
        """
        async with self.async_session() as session:
            query = select(KycDocumentModel).where(KycDocumentModel.id == document_id)
            if organization_id is not None:
                query = query.where(KycDocumentModel.organization_id == organization_id)
            return await session.scalar(query)

    async def delete_document(
        self, document_id: int, *, organization_id: int
    ) -> str | None:
        """Remove a document row, scoped to its owner.

        Returns the storage key so the caller can delete the stored object too.
        A key rather than the model, because the row is gone by the time the
        caller sees it — reading any attribute off a deleted instance raises.
        """
        async with self.async_session() as session:
            document = await session.scalar(
                select(KycDocumentModel).where(
                    KycDocumentModel.id == document_id,
                    KycDocumentModel.organization_id == organization_id,
                )
            )
            if document is None:
                return None
            storage_key = document.storage_key
            await session.delete(document)
            await session.commit()
            return storage_key

    async def list_kyc_for_review(
        self, *, statuses: list[str] | None = None, limit: int = 100
    ) -> list[tuple[OrganizationKycModel, OrganizationModel]]:
        """The staff review queue, oldest submission first.

        Oldest-first because the queue is a promise about turnaround time, and
        newest-first would let an old submission starve indefinitely.
        """
        async with self.async_session() as session:
            query = (
                select(OrganizationKycModel, OrganizationModel)
                .join(
                    OrganizationModel,
                    OrganizationKycModel.organization_id == OrganizationModel.id,
                )
                .options(selectinload(OrganizationKycModel.documents))
                .order_by(OrganizationKycModel.submitted_at.asc().nulls_last())
                .limit(limit)
            )
            if statuses:
                query = query.where(OrganizationKycModel.status.in_(statuses))
            return list((await session.execute(query)).all())

    async def list_kyc_awaiting_carrier(
        self, *, limit: int = 200
    ) -> list[OrganizationKycModel]:
        """Records the carrier still owes us a decision on.

        Drives the status poll. Ordered by when we forwarded, so the longest
        wait is chased first.
        """
        async with self.async_session() as session:
            return list(
                (
                    await session.scalars(
                        select(OrganizationKycModel)
                        .where(
                            OrganizationKycModel.status == KycStatus.FORWARDED.value,
                            OrganizationKycModel.carrier_reference.isnot(None),
                        )
                        .order_by(OrganizationKycModel.forwarded_at.asc().nulls_last())
                        .limit(limit)
                    )
                ).all()
            )
