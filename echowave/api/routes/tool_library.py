"""API for the ready-made integration tools.

The catalogue behind "add a Zoho tool" on the tools screen. See
`services/integrations/tool_library.py` for what is in it, and why the entries
seed an ordinary `http_api` tool rather than a category of their own.

Endpoints:
    GET /tool-library                     → the catalogue and its vendors
    GET /tool-library/{key}/definition    → the tool definition one entry seeds
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from api.db.models import UserModel
from api.sdk_expose import sdk_expose
from api.services.auth.depends import get_user
from api.services.integrations.tool_library import (
    ToolLibraryResponse,
    find,
    library,
    seed_definition,
)

router = APIRouter(prefix="/tool-library")


@router.get(
    "",
    response_model=ToolLibraryResponse,
    **sdk_expose(
        method="get_tool_library",
        description=(
            "Ready-made tools for the apps businesses run on, grouped by vendor."
        ),
    ),
)
async def get_tool_library(
    _user: UserModel = Depends(get_user),
) -> ToolLibraryResponse:
    """The whole catalogue.

    Authenticated but not org-scoped: it is identical for everyone and holds no
    tenant data. Behind auth because it is a product surface, not a secret.
    """
    return library()


@router.get(
    "/{key}/definition",
    **sdk_expose(
        method="get_tool_library_definition",
        description=(
            "The http_api tool definition a catalogue entry seeds, ready to "
            "create a tool from."
        ),
    ),
)
async def get_tool_library_definition(
    key: str,
    credential_uuid: str | None = Query(
        default=None,
        description=(
            "The OAuth credential to attach. Optional here so the shape can be "
            "previewed before an account is connected."
        ),
    ),
    _user: UserModel = Depends(get_user),
) -> dict:
    """What creating this tool would produce.

    Returned rather than created, deliberately. The operator edits the URL for
    their datacentre and reviews the parameters before anything is saved, and a
    catalogue entry that wrote a live tool on a GET would be a surprising thing
    for a browse action to do.
    """
    entry = find(key)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No catalogue entry '{key}'")
    return {
        "key": entry.key,
        "name": entry.tool_name,
        "description": entry.tool_description,
        "setup_note": entry.setup_note,
        "definition": seed_definition(entry, credential_uuid=credential_uuid),
    }
