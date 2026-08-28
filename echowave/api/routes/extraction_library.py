"""API for the ready-made extraction catalog.

A field whose ``renderer_options.library.catalog`` names a catalog is offered a
"Browse library" control in the node editor; this is where that control reads
from. See ``services/workflow/extraction_library.py`` for what is in it and why
the prompts live on the backend.

Endpoints:
    GET /extraction-library/{catalog}  → the catalog's entries and sections
"""

from fastapi import APIRouter, Depends, HTTPException

from api.db.models import UserModel
from api.sdk_expose import sdk_expose
from api.services.auth.depends import get_user
from api.services.workflow.extraction_library import (
    ExtractionLibraryResponse,
    get_library,
)

router = APIRouter(prefix="/extraction-library")


@router.get(
    "/{catalog}",
    response_model=ExtractionLibraryResponse,
    **sdk_expose(
        method="get_extraction_library",
        description=(
            "Ready-made extractions for a catalog named by a node property's "
            "renderer_options.library.catalog."
        ),
    ),
)
async def get_extraction_library(
    catalog: str,
    _user: UserModel = Depends(get_user),
) -> ExtractionLibraryResponse:
    """One catalog of ready-made extractions.

    Authenticated but not org-scoped: the catalog is the same for everyone and
    holds no tenant data. It is behind auth because it is a product surface,
    not because any of it is a secret.
    """
    try:
        return get_library(catalog)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown catalog {catalog!r}")
