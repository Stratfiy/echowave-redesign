"""The trust page's facts, for anybody, without an account.

A customer's security review happens before they sign up, not after: the
person asking where the audio goes and who else touches it is deciding
whether to start, and a page they can only read from inside the product is a
page they never reach. So this route takes no authentication and says nothing
account-specific -- it is the platform's own posture, identical for every
reader.

Everything here is derived from the code that does the thing, never typed out
beside it. The sub-processor list comes from
``services/privacy/subprocessors.in_use``: the providers this deployment holds
keys for, plus anything a call was actually billed against in the last ninety
days. Retention comes from the same constants the deletion job reads. A trust
page whose claims are maintained by hand is a set of specific written promises
that go stale the first time somebody adds a vendor and forgets, which is
worse than having no page at all.

The per-account answer -- who processed *your* calls -- stays where it was, on
/privacy behind a login. This one cannot be that: answering it for an
unauthenticated asker would itself be a disclosure.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.constants import (
    DEFAULT_RECORDING_RETENTION_DAYS,
    DEFAULT_TRANSCRIPT_RETENTION_DAYS,
    GRIEVANCE_OFFICER_ADDRESS,
    GRIEVANCE_OFFICER_EMAIL,
    GRIEVANCE_OFFICER_NAME,
    S3_REGION,
)
from api.db import db_client
from api.services.privacy import subprocessors

router = APIRouter(prefix="/public/trust", tags=["public-trust"])


@router.get("")
async def trust() -> dict[str, Any]:
    """What the platform does with the data, and who else sees it."""
    async with db_client.async_session() as session:
        listed = await subprocessors.in_use(session)

    return {
        "region": S3_REGION,
        "retention": {
            "recording_days": DEFAULT_RECORDING_RETENTION_DAYS,
            "transcript_days": DEFAULT_TRANSCRIPT_RETENTION_DAYS,
            # The window the list below is built from, so a reader can tell
            # what "in use" means rather than guessing at it.
            "subprocessor_window_days": subprocessors.IN_USE_WINDOW_DAYS,
        },
        "subprocessors": [
            {
                "name": s.name,
                "purpose": s.purpose,
                "data": s.data,
                "basis": s.basis,
            }
            for s in listed
        ],
        "grievance_officer": {
            "name": GRIEVANCE_OFFICER_NAME or None,
            "email": GRIEVANCE_OFFICER_EMAIL or None,
            "address": GRIEVANCE_OFFICER_ADDRESS or None,
        },
    }
