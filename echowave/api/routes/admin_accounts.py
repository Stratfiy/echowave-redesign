"""Staff standing up an account for a customer.

**Every route is staff-only**, declared once at router level so a new endpoint
added here is gated by default rather than by remembering.

Distinct from ``POST /organizations/members``, which is an owner adding a
colleague to an organization that already exists. This creates the account
itself — the case where somebody has been sold a subscription over a call and
should find it waiting for them rather than being told to go and sign up.

No password is set here, and none is generated. The invitation carries a code,
the customer chooses their own password, and until they do the account cannot
be signed into at all. A temporary password mailed out is a working credential
sitting in an inbox for as long as the mailbox exists, and the customer who
never changes it is the one whose account is taken.

The organization is provisioned **on acceptance**, not here, so an invitation
nobody claims leaves no empty account behind to reconcile later.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.services.auth import invitations
from api.services.auth.depends import get_superuser
from api.services.messaging.email import send_email

router = APIRouter(
    prefix="/admin/accounts",
    tags=["admin-accounts"],
    dependencies=[Depends(get_superuser)],
)


class CreateAccountRequest(BaseModel):
    email: str = Field(..., max_length=320, description="The customer's address.")
    note: str | None = Field(
        None,
        max_length=500,
        description="Recorded for staff. Never shown to the customer.",
    )


@router.post("")
async def create_account(
    request: CreateAccountRequest, user: UserModel = Depends(get_superuser)
) -> dict[str, Any]:
    """Create an account for a customer and email them a code to claim it."""
    async with db_client.async_session() as session:
        try:
            issued = await invitations.issue(
                session,
                email=request.email,
                # None on purpose: staff are creating a *new* account, not
                # adding somebody to one of ours. The organization is
                # provisioned when the invitation is accepted.
                organization_id=None,
                invited_by=user.id,
            )
        except invitations.InvitationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()

    result = await send_email(
        to=issued.email,
        subject=invitations.notice_subject(),
        body_text=invitations.notice_body(issued, invited_by_email=None),
    )
    # A failed send is reported rather than swallowed. The invitation is
    # staged either way, and staff who are not told will tell a customer to
    # check an inbox nothing was sent to.
    return {
        "email": issued.email,
        "expires_at": issued.expires_at.isoformat(),
        "email_sent": result.ok,
        "email_error": result.error,
    }
