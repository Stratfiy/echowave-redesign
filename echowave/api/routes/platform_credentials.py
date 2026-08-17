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
from api.services.configuration import key_validation
from api.services.configuration import platform_credentials as creds
from api.services.configuration.registry import components_for_provider

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
    apply_to_all_components: bool = Field(
        False,
        description="Store this key for every component this provider serves.",
    )
    verify: bool = Field(
        True,
        description=(
            "Check the key against the vendor before storing it. A rejected "
            "key is refused; a vendor we cannot reach is stored and reported "
            "as unverified."
        ),
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
    }


@router.put("")
async def set_provider_key(
    request: SetCredentialRequest, user: UserModel = Depends(get_superuser)
) -> dict[str, Any]:
    """Store or rotate one key. The value is write-only from here on.

    Same two behaviours as the customer vault, and for the same reasons: the key
    is checked against the vendor first and a rejected one is refused, and
    ``apply_to_all_components`` stores it against every component that vendor
    serves in one transaction. A platform key is worse to get wrong than a
    customer's — it is what every managed account runs on — so it is not the
    place to have the weaker of the two checks.
    """
    validation = (
        await key_validation.validate_key(request.provider, request.api_key)
        if request.verify
        else key_validation.ValidationResult(
            "unverified", "The key was stored without being checked."
        )
    )
    if not validation.may_store:
        raise HTTPException(status_code=400, detail=validation.message)

    components = [request.component]
    if request.apply_to_all_components:
        serves = components_for_provider(request.provider)
        # The requested component leads, so it is the one reported back. An
        # unknown provider falls through to the single component and lets
        # set_credential reject it, rather than silently storing nothing.
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
        # One commit for the whole set: a key that landed on two of three
        # components would leave the platform half-configured, with the failure
        # that caused it invisible.
        await session.commit()

    return {
        **_view(stored[0]),
        "applied_to": [c.component for c in stored],
        "verification": validation.outcome,
        "verification_message": validation.message,
    }


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
