"""The do-not-disturb list a customer maintains for their own account.

Scoped to the caller's own organization on every route, like the rest of the
privacy surface. These rows are personal data about the people the customer
called: answering "is this number on your list" for anyone but that customer
would itself be a disclosure.

The list is *ours to enforce*, not ours to own. Under DPDP the customer is the
Data Fiduciary for the people they call; we are the Processor. So this API lets
them record and remove suppressions, and services/compliance/dnd.py refuses the
call. Neither half is useful alone.
"""

from __future__ import annotations

import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.compliance.dnd import normalise_number

router = APIRouter(prefix="/do-not-call", tags=["compliance"])

# An uploaded list is a file someone exported from a CRM, not an API payload.
# The cap is here so a mis-selected 2GB file fails fast with a sentence rather
# than by exhausting the worker.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_NUMBERS_PER_REQUEST = 10_000


class DoNotCallEntry(BaseModel):
    phone_number: str
    source: str
    note: Optional[str] = None
    created_at: Optional[str] = None


class AddNumbersRequest(BaseModel):
    phone_numbers: list[str] = Field(..., min_length=1)
    note: Optional[str] = Field(default=None, max_length=255)


class AddNumbersResponse(BaseModel):
    added: int
    already_listed: int
    rejected: list[str]


class ListResponse(BaseModel):
    total: int
    entries: list[DoNotCallEntry]


def _partition(raw_numbers: list[str]) -> tuple[list[str], list[str]]:
    """Split input into storable keys and things that are not phone numbers.

    Rejects are returned rather than dropped. A customer who uploads 4,000 rows
    and is told "4,000 added" when 300 were unusable believes they are covered
    for 300 numbers they are not.
    """
    accepted: list[str] = []
    rejected: list[str] = []
    for raw in raw_numbers:
        normalised = normalise_number(raw)
        if normalised is None:
            rejected.append(raw)
        else:
            accepted.append(normalised)
    return accepted, rejected


@router.get("", response_model=ListResponse)
async def list_numbers(
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    user: UserModel = Depends(get_user),
) -> ListResponse:
    organization_id = user.selected_organization_id
    entries = await db_client.list_dnd_entries(
        organization_id, limit=limit, offset=offset
    )
    total = await db_client.count_dnd_entries(organization_id)
    return ListResponse(
        total=total,
        entries=[
            DoNotCallEntry(
                phone_number=entry.phone_number,
                source=entry.source,
                note=entry.note,
                created_at=entry.created_at.isoformat() if entry.created_at else None,
            )
            for entry in entries
        ],
    )


@router.post("", response_model=AddNumbersResponse)
async def add_numbers(
    request: AddNumbersRequest,
    user: UserModel = Depends(get_user),
) -> AddNumbersResponse:
    if len(request.phone_numbers) > MAX_NUMBERS_PER_REQUEST:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Send at most {MAX_NUMBERS_PER_REQUEST} numbers per request, "
                "or upload a CSV."
            ),
        )

    accepted, rejected = _partition(request.phone_numbers)
    added = await db_client.add_dnd_entries(
        user.selected_organization_id,
        accepted,
        source="manual",
        note=request.note,
        created_by=user.id,
    )
    return AddNumbersResponse(
        added=added,
        already_listed=len(set(accepted)) - added,
        rejected=rejected,
    )


@router.post("/upload", response_model=AddNumbersResponse)
async def upload_numbers(
    file: UploadFile = File(...),
    user: UserModel = Depends(get_user),
) -> AddNumbersResponse:
    """Bulk upload from a CSV.

    Every cell of every row is considered, not just a named column. The files
    people actually have are exports with the number in whichever column that
    CRM chose, often with no header at all, and rejecting them for having the
    wrong shape means the list does not get uploaded — which is a compliance
    outcome, not a UX one. Anything that does not normalise to a phone number
    is reported back rather than silently ignored.
    """
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)}MB.",
        )

    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Excel on Windows still writes cp1252 more often than anyone expects.
        try:
            text = contents.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail="Could not read the file as text. Save it as CSV (UTF-8).",
            ) from exc

    candidates: list[str] = []
    for row in csv.reader(io.StringIO(text)):
        candidates.extend(cell for cell in row if cell and cell.strip())

    if not candidates:
        raise HTTPException(status_code=400, detail="The file has no rows.")
    if len(candidates) > MAX_NUMBERS_PER_REQUEST:
        raise HTTPException(
            status_code=413,
            detail=(
                f"The file has more than {MAX_NUMBERS_PER_REQUEST} values. "
                "Split it and upload in parts."
            ),
        )

    accepted, rejected = _partition(candidates)
    if not accepted:
        raise HTTPException(
            status_code=400,
            detail="No phone numbers were found in that file.",
        )

    added = await db_client.add_dnd_entries(
        user.selected_organization_id,
        accepted,
        source="upload",
        note=file.filename[:255] if file.filename else None,
        created_by=user.id,
    )
    return AddNumbersResponse(
        added=added,
        already_listed=len(set(accepted)) - added,
        # Header cells and stray columns land here. Capped so a wide export
        # does not return a response larger than the file that produced it.
        rejected=rejected[:50],
    )


@router.delete("/{phone_number}")
async def remove_number(
    phone_number: str,
    user: UserModel = Depends(get_user),
) -> dict:
    """Take a number off the list.

    Normalised on the way in for the same reason it is normalised on the way:
    the caller should be able to delete using whatever form they have, not the
    canonical form they never saw.
    """
    normalised = normalise_number(phone_number)
    if normalised is None:
        raise HTTPException(status_code=400, detail="Not a phone number.")

    removed = await db_client.remove_dnd_entry(
        user.selected_organization_id, normalised
    )
    if not removed:
        raise HTTPException(status_code=404, detail="That number is not on the list.")
    return {"removed": normalised}
