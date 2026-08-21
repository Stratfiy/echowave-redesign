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


class OfferedModelsRequest(BaseModel):
    """Exactly the models Decibyl offers for this slot on this provider.

    A replace, not a merge: the screen is a set of tick boxes and an unticked
    box means "we do not offer this". Merging would make removal impossible
    from the only UI that writes here.
    """

    component: str = Field(..., description="stt | llm | tts | realtime | embeddings")
    provider: str = Field(..., min_length=1, max_length=64)
    models: list[str] = Field(default_factory=list)
    #: Customer-facing names, keyed by model id. Anything unnamed shows its id.
    labels: dict[str, str] = Field(default_factory=dict)


@router.get("/models")
async def discover_models(
    component: str,
    provider: str,
) -> dict[str, Any]:
    """The models this provider serves, asked with the key we hold.

    The point of the screen this serves: an operator who has just installed a
    key should not have to go and read the vendor's documentation to find out
    what they bought.

    Falls back to the models this codebase already knows about when the vendor
    has no list endpoint or cannot be reached, and says which of the two
    happened — "these are the models OpenAI told us about" and "these are the
    ones we have heard of" are different claims.
    """
    from api.services.configuration import model_catalogue, model_discovery

    async with db_client.async_session() as session:
        api_key = await creds.resolve_api_key(
            session, component=component, provider=provider
        )
        # Realtime and embeddings authenticate on the LLM credential — the
        # vendor issues one key for all of them.
        if api_key is None and component in ("realtime", "embeddings"):
            api_key = await creds.resolve_api_key(
                session, component="llm", provider=provider.replace("_realtime", "")
            )
        already = await model_catalogue.offers(
            session, component=component, provider=provider
        )

    found = await model_discovery.discover(
        component=component, provider=provider, api_key=api_key
    )

    return {
        "provider": found.provider,
        "component": found.component,
        "source": found.source,
        "note": found.note,
        "has_key": api_key is not None,
        "allow_custom_model": model_discovery.allows_custom_model(component, provider),
        "models": [
            {
                "id": model.id,
                "suggested": model.suggested,
                "offered": model.id in already,
            }
            for model in found.models
        ],
    }


@router.get("/catalogue")
async def list_catalogue() -> dict[str, Any]:
    """Everything we offer, and what each row still needs to be sellable.

    ``is_priced`` is the one that matters and the one that used to be
    invisible: an offered model with no rate row does not fail, it bills the
    platform fee alone and reports margin we did not earn.
    """
    from api.services.configuration import model_catalogue

    async with db_client.async_session() as session:
        entries = await model_catalogue.list_catalogue(session)

    return {
        "models": [
            {
                "component": entry.component,
                "provider": entry.provider,
                "model": entry.model,
                "label": entry.label,
                "enabled": entry.enabled,
                "is_priced": entry.is_priced,
                "priced_by_fallback": entry.priced_by_fallback,
                "has_key": entry.has_key,
                "is_sellable": entry.is_sellable,
            }
            for entry in entries
        ]
    }


@router.put("/models")
async def set_offered_models(request: OfferedModelsRequest) -> dict[str, Any]:
    """Put a set of models on sale for one slot on one provider.

    Nothing here checks that a model is priced. That is deliberate: a vendor's
    new model routinely lands before anybody prices it, and refusing would make
    the price book a gate on shipping. The listing above reports the gap and
    the customer-facing picker omits anything unpriced, which is the honest
    combination — visible to us, invisible to them.
    """
    from api.services.configuration import model_catalogue

    async with db_client.async_session() as session:
        try:
            entries = await model_catalogue.set_offered(
                session,
                component=request.component,
                provider=request.provider,
                models=request.models,
                labels=request.labels,
            )
        except model_catalogue.CatalogueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()

    return {
        "models": [
            {
                "component": entry.component,
                "provider": entry.provider,
                "model": entry.model,
                "label": entry.label,
                "is_priced": entry.is_priced,
                "has_key": entry.has_key,
                "is_sellable": entry.is_sellable,
            }
            for entry in entries
        ]
    }
