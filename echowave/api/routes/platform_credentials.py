"""Staff management of Decibyl's own provider API keys.

**Every route is staff-only**, declared once at router level so a new endpoint
added here is gated by default rather than by remembering.

There is no read path for a stored key. Responses carry the last four
characters and nothing more — an operator needs to confirm *which* key is
installed, never to retrieve it. Retrieval would turn this screen into a way to
exfiltrate every provider key on the platform from a single compromised staff
session.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_superuser
from api.services.configuration import platform_credentials as creds
from api.services.configuration.registry import (
    components_keyed_by_api_key,
    provider_component_map,
)

router = APIRouter(
    prefix="/admin/provider-keys",
    tags=["admin-provider-keys"],
    dependencies=[Depends(get_superuser)],
)


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


def _view(credential: creds.PlatformCredential) -> dict[str, Any]:
    return {
        "id": credential.id,
        "component": credential.component,
        "provider": credential.provider,
        "masked_key": credential.masked_key,
        "label": credential.label,
        "is_active": credential.is_active,
        "updated_at": credential.updated_at,
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
        "provider_components": provider_component_map(),
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
        # Only the components this key can actually authenticate. Google does
        # all three, but Cloud Speech and TTS want a service-account JSON, so
        # fanning an API key onto them would store something they cannot read.
        serves = components_keyed_by_api_key(request.provider)
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
