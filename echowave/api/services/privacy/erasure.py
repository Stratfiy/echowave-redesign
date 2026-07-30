"""Deleting one person's data on request.

DPDP s12(3) and GDPR Art 17 both give a person the right to have their data
erased. For a voice platform that right is usually exercised by somebody who
never signed up: the person who answered the phone. They are a Data Principal
under DPDP and a data subject under GDPR, our customer is the Fiduciary or
Controller, and we are the Processor acting on the customer's instruction.

So erasure is initiated **by the customer, for their own calls**. We do not act
on a request from a stranger about an account we do not know they belong to —
that would itself be a disclosure, since confirming a number appears in an
account tells the asker something about that account.

Two things this module refuses to do quietly:

* **It does not erase what the law requires us to keep.** Duration and cost stay
  on the run so a GST return still reconciles; the conversation goes. GDPR
  Art 17(3)(b) exempts processing required for a legal obligation, and the
  retained figures identify nobody.
* **It does not report success until the objects are gone.** A deletion that
  clears the database row and leaves the audio in a bucket is worse than no
  deletion at all, because it produces a confident answer to "have you deleted
  my recording" that happens to be false.

Phone numbers are matched against the context stored on each run, and the
request itself records only a **hash** of the number. A register of people who
asked to be forgotten would be its own personal data, and a sensitive one.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    ErasureRequestModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.privacy.retention import PURGED_MARKER, _delete_objects, _storage_keys


class ErasureError(ValueError):
    """An erasure request could not be carried out."""


@dataclass(frozen=True)
class ErasureResult:
    request_id: int
    runs_affected: int
    objects_deleted: int
    status: str


def normalise_number(raw: str) -> str:
    """Reduce a phone number to comparable digits.

    Numbers reach us in whatever shape the carrier or the customer's CSV used —
    ``+91 98765 43210``, ``09876543210``, ``9876543210``. Someone asking to be
    forgotten will not know which. Matching on digits alone, ignoring a leading
    country or trunk code, is what makes the request work rather than silently
    matching nothing.
    """
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        raise ErasureError("That does not look like a phone number.")
    # Compare on the last ten digits, which is the subscriber number in India
    # and long enough elsewhere to avoid collisions in one account's traffic.
    return digits[-10:] if len(digits) >= 10 else digits


def hash_subject(value: str) -> str:
    """Stable hash for the audit record, so no number is stored in the clear."""
    return hashlib.sha256(value.encode()).hexdigest()


async def find_runs_for_number(
    session: AsyncSession, *, organization_id: int, number: str
) -> list[WorkflowRunModel]:
    """Every call in this account involving a number.

    Scoped to one organization, always. A cross-account search would let a
    customer discover another customer's traffic.
    """
    digits = normalise_number(number)

    # Phone numbers live inside the context JSON rather than in columns, so this
    # matches on the serialised value — but it strips punctuation from the
    # *stored* side too, not only from the search term. A number saved as
    # "+91 98765 43210" contains no substring "9876543210" until the spaces are
    # gone, so normalising one side only silently matches nothing and reports a
    # successful erasure of zero calls.
    def digits_of(column):
        return func.regexp_replace(cast(column, String), r"[^0-9]", "", "g")

    runs = (
        await session.scalars(
            select(WorkflowRunModel)
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(
                WorkflowModel.organization_id == organization_id,
                or_(
                    digits_of(WorkflowRunModel.initial_context).contains(digits),
                    digits_of(WorkflowRunModel.gathered_context).contains(digits),
                ),
            )
        )
    ).all()
    return list(runs)


async def erase_number(
    session: AsyncSession,
    *,
    organization_id: int,
    number: str,
    requested_by: int | None = None,
) -> ErasureResult:
    """Erase every trace of one person from an account's calls.

    The request row is written first and updated at the end, so a crash midway
    leaves evidence that a request arrived rather than nothing at all — which
    matters when the obligation is to respond within a deadline.
    """
    digits = normalise_number(number)

    request = ErasureRequestModel(
        organization_id=organization_id,
        subject_type="phone_number",
        subject_hash=hash_subject(digits),
        status="pending",
        requested_by=requested_by,
    )
    session.add(request)
    await session.flush()

    runs = await find_runs_for_number(
        session, organization_id=organization_id, number=number
    )

    objects_deleted = 0
    runs_affected = 0
    failures = 0

    for run in runs:
        deleted, failed = await _delete_objects(_storage_keys(run))
        objects_deleted += deleted
        if failed:
            failures += len(failed)
            # Leave the row pointing at what is still there, so a retry can
            # finish the job rather than losing track of it.
            continue

        run.recording_url = PURGED_MARKER
        run.transcript_url = PURGED_MARKER
        run.initial_context = {}
        run.gathered_context = {}
        run.logs = {}
        run.extra = {
            k: v
            for k, v in (run.extra or {}).items()
            if k not in {"recording_artifacts", "recordings"}
        }
        # Duration and cost stay. A GST return has to keep reconciling, and
        # "this call lasted 94 seconds and cost Rs 3.20" identifies nobody.
        runs_affected += 1

    request.runs_affected = runs_affected
    request.objects_deleted = objects_deleted
    request.completed_at = datetime.now(UTC)
    request.status = "completed" if failures == 0 else "failed"
    if failures:
        request.note = (
            f"{failures} object(s) could not be deleted from storage; "
            "the affected runs were left intact for a retry."
        )

    await session.flush()

    logger.info(
        "Erasure for org {}: {} run(s), {} object(s), status {}",
        organization_id,
        runs_affected,
        objects_deleted,
        request.status,
    )
    return ErasureResult(
        request_id=request.id,
        runs_affected=runs_affected,
        objects_deleted=objects_deleted,
        status=request.status,
    )


async def erase_organization(
    session: AsyncSession, *, organization_id: int, requested_by: int | None = None
) -> ErasureResult:
    """Erase every call recording, transcript and context an account holds.

    For an account closing down. Deliberately leaves the organization row, its
    users and its billing history: a closed account still has invoices that must
    survive, and deleting the ledger would destroy the record of money that
    genuinely changed hands.
    """
    request = ErasureRequestModel(
        organization_id=organization_id,
        subject_type="organization",
        status="pending",
        requested_by=requested_by,
    )
    session.add(request)
    await session.flush()

    runs = (
        await session.scalars(
            select(WorkflowRunModel)
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(WorkflowModel.organization_id == organization_id)
        )
    ).all()

    objects_deleted = 0
    runs_affected = 0
    failures = 0

    for run in runs:
        deleted, failed = await _delete_objects(_storage_keys(run))
        objects_deleted += deleted
        if failed:
            failures += len(failed)
            continue

        run.recording_url = PURGED_MARKER
        run.transcript_url = PURGED_MARKER
        run.initial_context = {}
        run.gathered_context = {}
        run.logs = {}
        run.extra = {}
        runs_affected += 1

    request.runs_affected = runs_affected
    request.objects_deleted = objects_deleted
    request.completed_at = datetime.now(UTC)
    request.status = "completed" if failures == 0 else "failed"
    if failures:
        request.note = f"{failures} object(s) could not be deleted from storage."

    await session.flush()

    logger.info(
        "Organization erasure for org {}: {} run(s), {} object(s), status {}",
        organization_id,
        runs_affected,
        objects_deleted,
        request.status,
    )
    return ErasureResult(
        request_id=request.id,
        runs_affected=runs_affected,
        objects_deleted=objects_deleted,
        status=request.status,
    )


async def list_requests(
    session: AsyncSession, *, organization_id: int, limit: int = 100
) -> list[dict]:
    """This account's erasure requests, newest first.

    The evidence that obligations were met, which is why completed requests are
    kept: the record of an erasure is not itself the erased data.
    """
    rows = (
        await session.scalars(
            select(ErasureRequestModel)
            .where(ErasureRequestModel.organization_id == organization_id)
            .order_by(ErasureRequestModel.requested_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "id": r.id,
            "subject_type": r.subject_type,
            "status": r.status,
            "requested_at": r.requested_at.isoformat() if r.requested_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "runs_affected": r.runs_affected,
            "objects_deleted": r.objects_deleted,
            "note": r.note,
        }
        for r in rows
    ]
