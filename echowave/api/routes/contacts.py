"""Contact lists an inbound number matches its callers against.

Thin by design (see ``api/AGENTS.md``): parse, resolve the organization,
delegate. Parsing an uploaded CSV lives in ``services/contacts``, the writes in
``db/contact_client``, and the decision about who gets through in
``services/telephony/inbound_guard``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.sdk_expose import sdk_expose
from api.services.auth.depends import get_user
from api.services.contacts import MAX_CONTACT_ROWS, parse_contacts_csv

router = APIRouter(prefix="/contact-lists")

#: Refuse an upload larger than this without reading it. The row cap is the
#: real limit; this stops a 2GB file being buffered to find that out.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class ContactListRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = None


class ContactListResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    contact_count: int = 0


class ContactResponse(BaseModel):
    id: int
    phone_raw: str
    phone_normalized: str
    name: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)


class ContactsPage(BaseModel):
    contacts: List[ContactResponse]
    total_count: int


class ImportResponse(BaseModel):
    """What the import did, in the terms the person who uploaded it asked in."""

    imported: int
    skipped: int
    #: ``"line 14: '+91 98' is not a usable phone number"``. Capped in the
    #: parser — a wholly broken file says so once rather than 50,000 times.
    problems: List[str] = Field(default_factory=list)
    phone_column: Optional[str] = None
    truncated: bool = False


def _as_response(row, contact_count: int = 0) -> ContactListResponse:
    return ContactListResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        contact_count=contact_count,
    )


@router.get(
    "",
    response_model=List[ContactListResponse],
    **sdk_expose(
        method="list_contact_lists",
        description="List the organization's contact lists.",
    ),
)
async def list_contact_lists(
    user: UserModel = Depends(get_user),
) -> List[ContactListResponse]:
    org_id = user.selected_organization_id
    rows = await db_client.get_contact_lists(organization_id=org_id)
    return [
        _as_response(
            row, await db_client.count_contacts(row.id, organization_id=org_id)
        )
        for row in rows
    ]


@router.post(
    "",
    response_model=ContactListResponse,
    **sdk_expose(
        method="create_contact_list",
        description="Create a contact list.",
    ),
)
async def create_contact_list(
    payload: ContactListRequest,
    user: UserModel = Depends(get_user),
) -> ContactListResponse:
    row = await db_client.create_contact_list(
        organization_id=user.selected_organization_id,
        name=payload.name.strip(),
        description=payload.description,
    )
    return _as_response(row)


@router.patch("/{contact_list_id}", response_model=ContactListResponse)
async def update_contact_list(
    contact_list_id: int,
    payload: ContactListRequest,
    user: UserModel = Depends(get_user),
) -> ContactListResponse:
    org_id = user.selected_organization_id
    row = await db_client.update_contact_list(
        contact_list_id,
        organization_id=org_id,
        name=payload.name.strip(),
        description=payload.description,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Contact list not found")
    return _as_response(
        row, await db_client.count_contacts(row.id, organization_id=org_id)
    )


@router.delete("/{contact_list_id}")
async def delete_contact_list(
    contact_list_id: int,
    user: UserModel = Depends(get_user),
) -> dict:
    deleted = await db_client.delete_contact_list(
        contact_list_id, organization_id=user.selected_organization_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Contact list not found")
    # A phone number pointing at this list keeps working: the FK is SET NULL,
    # so the number goes back to treating every caller as unknown rather than
    # refusing them all.
    return {"status": "deleted"}


@router.get("/{contact_list_id}/contacts", response_model=ContactsPage)
async def list_contacts(
    contact_list_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    user: UserModel = Depends(get_user),
) -> ContactsPage:
    org_id = user.selected_organization_id
    if not await db_client.get_contact_list(contact_list_id, organization_id=org_id):
        raise HTTPException(status_code=404, detail="Contact list not found")

    rows, total = await db_client.get_contacts(
        contact_list_id,
        organization_id=org_id,
        limit=limit,
        offset=offset,
        search=search,
    )
    return ContactsPage(
        contacts=[
            ContactResponse(
                id=row.id,
                phone_raw=row.phone_raw,
                phone_normalized=row.phone_normalized,
                name=row.name,
                attributes=row.attributes or {},
            )
            for row in rows
        ],
        total_count=total,
    )


@router.post("/{contact_list_id}/import", response_model=ImportResponse)
async def import_contacts(
    contact_list_id: int,
    file: UploadFile = File(...),
    phone_column: Optional[str] = Query(
        None, description="Header to read numbers from. Guessed when omitted."
    ),
    country: Optional[str] = Query(
        None,
        min_length=2,
        max_length=2,
        description=(
            "ISO-2 country for resolving local numbers — '09876543210' becomes "
            "'+919876543210' with 'IN' and is unusable without it. Pass the "
            "country of the number this list will answer on."
        ),
    ),
    user: UserModel = Depends(get_user),
) -> ImportResponse:
    """Import a CSV into a list, replacing any caller already in it.

    Upsert rather than append: re-uploading a corrected export is the normal
    way somebody fixes a list, and appending would leave both versions in
    place with no way to tell which the agent will match.
    """
    org_id = user.selected_organization_id
    if not await db_client.get_contact_list(contact_list_id, organization_id=org_id):
        raise HTTPException(status_code=404, detail="Contact list not found")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"That file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)}MB. "
                f"A list of up to {MAX_CONTACT_ROWS:,} contacts fits well inside it."
            ),
        )

    # Taken from the caller rather than inferred. Organizations carry no
    # country of their own, and picking one for them is how an Indian list of
    # local numbers silently imports as unmatchable rows — every contact
    # stored, none of them ever recognised on a call.
    parsed = parse_contacts_csv(
        content, country_hint=country, phone_column=phone_column
    )
    if parsed.rows:
        await db_client.upsert_contacts(
            contact_list_id, organization_id=org_id, rows=parsed.rows
        )
    logger.info(
        "Imported {} contacts into list {} for org {} ({} skipped)",
        len(parsed.rows),
        contact_list_id,
        org_id,
        parsed.skipped,
    )

    return ImportResponse(
        imported=len(parsed.rows),
        skipped=parsed.skipped,
        problems=[
            f"line {line}: {why}" if line else why for line, why in parsed.problems
        ],
        phone_column=parsed.phone_column,
        truncated=parsed.truncated,
    )


@router.delete("/{contact_list_id}/contacts/{contact_id}")
async def delete_contact(
    contact_list_id: int,
    contact_id: int,
    user: UserModel = Depends(get_user),
) -> dict:
    org_id = user.selected_organization_id
    if not await db_client.get_contact_list(contact_list_id, organization_id=org_id):
        raise HTTPException(status_code=404, detail="Contact list not found")
    if not await db_client.delete_contact(contact_id, organization_id=org_id):
        raise HTTPException(status_code=404, detail="Contact not found")
    return {"status": "deleted"}
