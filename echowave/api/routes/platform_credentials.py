"""Staff management of Decibyl's own provider API keys.

**Every route is staff-only**, declared once at router level so a new endpoint
added here is gated by default rather than by remembering.

There is no read path for a stored key. Responses carry the last four
characters and nothing more — an operator needs to confirm *which* key is
installed, never to retrieve it. Retrieval would turn this screen into a way to
exfiltrate every provider key on the platform from a single compromised staff
session.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.services import secret_rotation
from api.services.auth.depends import get_superuser
from api.services.configuration import platform_credentials as creds
from api.services.configuration.registry import (
    components_sharing_credential,
    provider_credential_groups,
)

router = APIRouter(
    prefix="/admin/provider-keys",
    tags=["admin-provider-keys"],
    dependencies=[Depends(get_superuser)],
)

#: When a key starts being worth rotating. A year is not a security threshold
#: anybody can defend precisely; it is the point past which "when did we last
#: change this" has no answer, and a key nobody remembers setting is a key
#: nobody knows the reach of.
STALE_AFTER_DAYS = 365


class SetCredentialRequest(BaseModel):
    component: str = Field(..., description="stt | llm | tts")
    provider: str = Field(..., min_length=1, max_length=64)
    api_key: str = Field(..., min_length=8)
    label: str | None = Field(None, max_length=128)
    # A vendor account is one account. Sarvam serves all three components on a
    # single key, and so does ElevenLabs — storing it once per component is
    # three round trips to say one thing, and the third one is the one that
    # gets forgotten, leaving a managed tier pointing at a provider we hold no
    # key for.
    #
    # Off by default, matching the customer-facing vault: holding two keys with
    # one vendor on separate billing is a real arrangement, and silently
    # overwriting the other component's key would be worse than the typing.
    apply_to_all_components: bool = Field(
        False,
        description="Store this key for every component this provider serves.",
    )


class ActiveRequest(BaseModel):
    component: str
    provider: str
    is_active: bool


def _age_days(updated_at: str | None) -> int | None:
    if not updated_at:
        return None
    try:
        moment = datetime.fromisoformat(updated_at)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0, (datetime.now(UTC) - moment).days)


def _view(credential: creds.PlatformCredential) -> dict[str, Any]:
    age = _age_days(credential.updated_at)
    return {
        "id": credential.id,
        "component": credential.component,
        "provider": credential.provider,
        "masked_key": credential.masked_key,
        "label": credential.label,
        "is_active": credential.is_active,
        "updated_at": credential.updated_at,
        # Derived here rather than in the screen so "stale" means one thing
        # across every surface that asks.
        "age_days": age,
        "is_stale": age is not None and age >= STALE_AFTER_DAYS,
    }


@router.get("")
async def list_provider_keys() -> dict[str, Any]:
    async with db_client.async_session() as session:
        stored = await creds.list_credentials(session)
    return {
        "credentials": [_view(c) for c in stored],
        # Surfaced so the screen can explain why saving fails, rather than
        # showing a generic error on every attempt.
        "encryption_configured": creds.encryption_is_configured(),
        "components": [c.value for c in creds.CREDENTIAL_COMPONENTS],
        # Which vendors serve more than one slot, so the form can offer to
        # store one key against all of them. Read off the registry rather than
        # a list in the screen, which would drift the first time a vendor
        # gained a component.
        "provider_components": provider_credential_groups(),
        # How old each key is, and how many are old enough to be worth
        # rotating. Age is the one property of a key an operator cannot see by
        # looking at it, and it is the one that decides whether to act.
        "stale_after_days": STALE_AFTER_DAYS,
    }


@router.get("/rotation")
async def rotation_status() -> dict[str, Any]:
    """Where a rotation of the encryption secret has got to.

    Deliberately a count rather than a verdict. The number that has to reach
    zero is ``pending`` — rows still readable only under the previous key —
    and dropping that key while it is non-zero strands every one of them.
    """
    async with db_client.async_session() as session:
        try:
            state = await secret_rotation.status(session)
        except secret_rotation.SecretError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return state.as_dict()


@router.post("/rotation/reencrypt")
async def reencrypt(dry_run: bool = False) -> dict[str, Any]:
    """Rewrite every stored secret under the current key.

    Re-runnable and safe to run while serving: a row already on the current
    key is skipped, and a row that decrypts under neither is counted and left
    exactly as it was rather than overwritten.
    """
    async with db_client.async_session() as session:
        try:
            result = await secret_rotation.reencrypt_all(session, dry_run=dry_run)
        except secret_rotation.SecretError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not dry_run:
            await session.commit()
        state = await secret_rotation.status(session)

    return {
        "dry_run": dry_run,
        "rewritten": result.rewritten,
        "already_current": result.skipped,
        "unreadable": result.unreadable,
        "by_kind": result.by_kind,
        "status": state.as_dict(),
    }


@router.put("")
async def set_provider_key(
    request: SetCredentialRequest, user: UserModel = Depends(get_superuser)
) -> dict[str, Any]:
    """Store or rotate one key. The value is write-only from here on.

    With ``apply_to_all_components`` the same key is stored against every
    component this vendor serves, in one transaction — so a Sarvam key entered
    once covers speech-to-text, the language model and synthesis together.
    """
    components = [request.component]
    if request.apply_to_all_components:
        # Only the components this *same secret* can authenticate. Google
        # serves all three but with two different credentials — a Gemini key
        # fans out to nothing, a service-account JSON fans out across both
        # speech slots — so the group is the unit, not the vendor.
        serves = components_sharing_credential(request.provider, request.component)
        # The requested component leads, so it is the one reported back and the
        # one whose failure surfaces first. An unknown provider falls through to
        # the single component and lets set_credential reject it, rather than
        # silently storing nothing.
        components = [request.component] + [
            component for component in serves if component != request.component
        ]

    async with db_client.async_session() as session:
        stored = []
        try:
            for component in components:
                stored.append(
                    await creds.set_credential(
                        session,
                        actor_user_id=user.id,
                        component=component,
                        provider=request.provider,
                        api_key=request.api_key,
                        label=request.label,
                    )
                )
        except creds.PlatformCredentialError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # One commit for the whole set. A key that landed on two of three
        # components leaves the managed tier half-configured, and the call that
        # fails because of it fails at dial time with the vendor's own 401.
        await session.commit()

    return {**_view(stored[0]), "applied_to": [c.component for c in stored]}


@router.post("/active")
async def set_provider_key_active(request: ActiveRequest) -> dict[str, Any]:
    """Take a provider in or out of service without discarding its key."""
    async with db_client.async_session() as session:
        try:
            credential = await creds.set_active(
                session,
                component=request.component,
                provider=request.provider,
                is_active=request.is_active,
            )
        except creds.PlatformCredentialError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
    return _view(credential)


@router.delete("")
async def delete_provider_key(component: str, provider: str) -> dict[str, Any]:
    async with db_client.async_session() as session:
        try:
            await creds.delete_credential(
                session, component=component, provider=provider
            )
        except creds.PlatformCredentialError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        remaining = await creds.list_credentials(session)
    return {"credentials": [_view(c) for c in remaining]}
