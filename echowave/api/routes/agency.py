"""What an agency sees of the accounts it manages.

Deliberately narrow. An agency gets a roll-up — which accounts are theirs, what
those accounts were charged, what they earned on them — and nothing more. No
sign-in as a client, no access to their agents, no recordings, no transcripts.
Those are conversations with the client's own customers, and an agency
relationship is not consent to listen to them.

The organization is resolved from the session on every route. An agency asking
about a client it does not manage gets its own empty statement rather than
somebody else's numbers, because the query filters by the caller rather than
trusting an id in the request.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user_with_selected_organization
from api.services.billing import agency

router = APIRouter(prefix="/agency", tags=["agency"])


@router.get("")
async def get_agency_statement(
    user: UserModel = Depends(get_user_with_selected_organization),
) -> dict[str, Any]:
    """This agency's clients and commission. Empty for an ordinary account."""
    async with db_client.async_session() as session:
        return await agency.statement(
            session, agency_organization_id=user.selected_organization_id
        )
