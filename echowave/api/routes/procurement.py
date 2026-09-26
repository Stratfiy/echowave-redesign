"""The procurement register and its files (PROCUREMENT_DOCS_2026_09_ENABLED).

Thin: the rules live in ``services/documents``. Every route resolves the
organization from the session, never the path or the body, and is a 404
while the flag is off. A file is served as a short-lived presigned link to
the object store -- a redirect by default, or the link itself for a screen
that opens it in a new tab.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_user
from api.services.documents import register

router = APIRouter(
    prefix="/procurement",
    tags=["procurement"],
    dependencies=[Depends(features.require("procurement_docs"))],
)

#: How long a download link lasts. Minutes, not the seven days a link
#: handed to the model lasts: this one is minted on a click.
LINK_SECONDS = 15 * 60


class RegisterEntry(BaseModel):
    register_id: int
    kind: str
    number: str
    status: str
    counterparty: str | None = None
    counterparty_gstin: str | None = None
    reference: str | None = None
    amount: str | None = None
    amount_paise: int | None = None
    issue_date: str | None = None
    due_date: str | None = None
    overdue: bool = False
    files: list[str] = []


class RegisterList(BaseModel):
    entries: list[RegisterEntry]


class FileLink(BaseModel):
    url: str
    filename: str


@router.get("/documents", response_model=RegisterList)
async def list_procurement_documents(
    kind: str | None = None,
    status: str | None = None,
    due_before: str | None = None,
    user: UserModel = Depends(get_user),
) -> RegisterList:
    """This workspace's register of drafted and issued documents, newest first."""
    async with db_client.async_session() as session:
        try:
            rows = await register.list_rows(
                session,
                organization_id=user.selected_organization_id,
                kind=kind,
                status=status,
                due_before=due_before,
            )
        except register.RegisterError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        entries = [RegisterEntry(**register.summary(r)) for r in rows]
    return RegisterList(entries=entries)


@router.get(
    "/documents/{register_id}/files/{file}",
    response_model=FileLink,
    responses={307: {"description": "Redirect to the file"}},
)
async def download_procurement_file(
    register_id: int,
    file: Literal["docx", "pdf", "xlsx"],
    redirect: bool = Query(True, description="False returns the link as JSON."),
    user: UserModel = Depends(get_user),
):
    """One of an entry's files, for this workspace only."""
    from api.services import storage

    organization_id = user.selected_organization_id
    async with db_client.async_session() as session:
        row = await register.get(
            session, organization_id=organization_id, register_id=register_id
        )
    key = getattr(row, f"{file}_key", None) if row is not None else None
    # The key is checked against its owner as well as the row: a key that
    # somehow points outside this workspace's prefix is not served.
    if row is None or not register.key_belongs_to(organization_id, row.id, key):
        raise HTTPException(status_code=404, detail="Not Found")
    url = await storage.storage_fs.aget_signed_url(key, expiration=LINK_SECONDS)
    if not url:
        raise HTTPException(status_code=503, detail="The file link could not be made.")
    if redirect:
        return RedirectResponse(url, status_code=307)
    return FileLink(url=url, filename=key.rsplit("/", 1)[-1])
