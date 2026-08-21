"""A customer's own provider API keys — the BYOK vault.

The tenant-facing mirror of ``routes/platform_credentials.py``, which does the
same job for Decibyl's own keys behind a staff gate. Same absence of a read
path: responses carry the last four characters and nothing more. A customer
needs to confirm *which* key is installed, never to retrieve it, and retrieval
would turn one stolen session into every key the account holds.

**Every route resolves the organization from the caller's session**, never from
the request body. An API key is the single most damaging row to leak across
tenants, so the organization is not something the client gets to name.

This screen used to be three tabs — "Speech to Speech", "Decibyl" and "BYOK" —
which mixed up two unrelated questions: where keys come from, and how the
pipeline is shaped. Architecture and model choice now live on the agent, where
they are actually decided. This endpoint does one job: hold keys.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationRole
from api.services.auth.depends import get_user, require_organization_role
from api.services.configuration import key_validation
from api.services.configuration import organization_credentials as creds
from api.services.configuration.registry import components_for_provider

router = APIRouter(prefix="/provider-keys", tags=["provider-keys"])


class SetCredentialRequest(BaseModel):
    component: str = Field(..., description="stt | llm | tts")
    provider: str = Field(..., min_length=1, max_length=64)
    api_key: str = Field(..., min_length=8)
    label: str | None = Field(None, max_length=128)
    # A vendor account is usually one account. Sarvam and ElevenLabs each serve
    # all three components, and the same key works for all of them — so storing
    # it once per component is three round trips to say one thing.
    #
    # Off by default, because the separate-key case is real: an account can
    # hold two keys with one vendor on separate billing, and silently
    # overwriting the other component's key would be worse than the typing.
    apply_to_all_components: bool = Field(
        False,
        description="Store this key for every component this provider serves.",
    )
    # An escape hatch, not a default. A vendor we cannot reach already stores
    # the key and reports it unverified, so this exists only for the case where
    # the customer knows the probe itself is wrong — a proxied endpoint, a key
    # scoped so narrowly the probe's own call is refused.
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


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


def _view(credential: creds.OrganizationCredential) -> dict[str, Any]:
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
async def list_provider_keys(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """Every key this account holds, masked."""
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        stored = await creds.list_credentials(session, organization_id=organization_id)
    return {
        "credentials": [_view(c) for c in stored],
        # Surfaced so the screen can say why saving fails, rather than showing a
        # generic error on every attempt. A deployment with no
        # PLATFORM_CREDENTIAL_SECRET cannot store keys at all, and that is an
        # administrator's problem rather than the customer's mistake.
        "encryption_configured": creds.encryption_is_configured(),
        "components": [c.value for c in creds.CREDENTIAL_COMPONENTS],
    }


@router.put("")
async def set_provider_key(
    request: SetCredentialRequest,
    user: UserModel = Depends(require_organization_role(OrganizationRole.ADMIN)),
) -> dict[str, Any]:
    """Store or rotate one key. The value is write-only from here on.

    With ``apply_to_all_components`` the same key is stored against every
    component this vendor serves, in one transaction — so a Sarvam key entered
    once covers speech-to-text, the language model and synthesis together.

    The key is checked against the vendor first. A key the vendor **rejects** is
    refused here with the vendor's own complaint, rather than being stored to
    fail later on a live call where it looks like "the agent didn't answer". A
    key we simply could not check — no probe for that vendor, or the vendor is
    unreachable — is stored, and the response says so; our incompleteness or a
    vendor's outage should not stop a customer configuring their account.
    """
    organization_id = _organization_id(user)

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
        # The requested component leads, so it is the one reported back and the
        # one whose failure is surfaced first. An unknown provider falls through
        # to the single component and lets set_credential reject it, rather than
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
                        organization_id=organization_id,
                        actor_user_id=user.id,
                        component=component,
                        provider=request.provider,
                        api_key=request.api_key,
                        label=request.label,
                    )
                )
        except creds.OrganizationCredentialError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # One commit for the whole set: a key that landed on two of three
        # components would leave the account half-configured, and the failure
        # that caused it invisible.
        await session.commit()

    return {
        **_view(stored[0]),
        "applied_to": [c.component for c in stored],
        # So the screen can say "Connected" only when the vendor actually said
        # so, and "Stored, not verified" otherwise, rather than showing the same
        # tick for both.
        "verification": validation.outcome,
        "verification_message": validation.message,
    }


@router.post("/active")
async def set_provider_key_active(
    request: ActiveRequest,
    user: UserModel = Depends(require_organization_role(OrganizationRole.ADMIN)),
) -> dict[str, Any]:
    """Take a provider out of service without discarding its key.

    Useful while rotating at the vendor: the agents pointing at it fail over to
    managed rather than authenticating with a key that is being revoked.
    """
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        try:
            credential = await creds.set_active(
                session,
                organization_id=organization_id,
                component=request.component,
                provider=request.provider,
                is_active=request.is_active,
            )
        except creds.OrganizationCredentialError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
    return _view(credential)


@router.delete("")
async def delete_provider_key(
    component: str,
    provider: str,
    user: UserModel = Depends(require_organization_role(OrganizationRole.ADMIN)),
) -> dict[str, Any]:
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        try:
            await creds.delete_credential(
                session,
                organization_id=organization_id,
                component=component,
                provider=provider,
            )
        except creds.OrganizationCredentialError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        remaining = await creds.list_credentials(
            session, organization_id=organization_id
        )
    return {"credentials": [_view(c) for c in remaining]}


@router.get("/models")
async def discover_models(
    component: str,
    provider: str,
    user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    """The models this account's own key unlocks, minus the ones we provide.

    The escape hatch, and the shape of it is the product decision. Decibyl
    manages the providers it has curated, priced and holds keys for; a customer
    brings a key only for what we do **not** offer. So anything already in the
    managed catalogue is excluded here rather than shown: offering a
    key-shaped way to buy something we sell managed would be two prices for one
    thing, and the customer would be paying a vendor directly for a model whose
    price is already on their Decibyl invoice.

    Asked with their key, so the list is what their account can actually reach —
    a vendor tier that excludes a model would otherwise let them pick one that
    fails at session open, on a live call.
    """
    organization_id = user.selected_organization_id
    if organization_id is None:
        raise HTTPException(status_code=400, detail="No organization selected")

    from api.services.configuration import model_catalogue, model_discovery

    async with db_client.async_session() as session:
        api_key = await creds.resolve_api_key(
            session,
            organization_id=organization_id,
            component=component,
            provider=provider,
        )
        we_provide = await model_catalogue.offers(
            session, component=component, provider=provider
        )

    if api_key is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No {provider} key is stored for this account. Add one first "
                "and we will list what it can use."
            ),
        )

    found = await model_discovery.discover(
        component=component, provider=provider, api_key=api_key
    )

    available = [model for model in found.models if model.id not in we_provide]

    return {
        "provider": found.provider,
        "component": found.component,
        "source": found.source,
        "note": found.note,
        "allow_custom_model": model_discovery.allows_custom_model(component, provider),
        "models": [
            {"id": model.id, "suggested": model.suggested} for model in available
        ],
        # How many were hidden because we already sell them. Shown as a count
        # rather than a list: the point is to explain a short list, not to
        # advertise the managed catalogue from inside the BYOK screen.
        "provided_by_decibyl": len(
            [model for model in found.models if model.id in we_provide]
        ),
    }
