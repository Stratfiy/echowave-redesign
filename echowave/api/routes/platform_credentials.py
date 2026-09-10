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
from api.services.configuration import (
    credential_validation,
    key_validation,
    provider_balance,
)
from api.services.configuration import platform_credentials as creds
from api.services.configuration.registry import (
    components_for_provider,
    known_providers,
    realtime_provider_for,
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
        # Three states, not two: True accepted, False rejected, null never
        # asked. A screen that collapses null into False tells an operator a
        # working key is broken.
        "last_check_ok": credential.last_check_ok,
        "last_checked_at": credential.last_checked_at,
        "last_check_error": credential.last_check_error,
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


@router.get("/providers")
async def list_known_providers() -> dict[str, Any]:
    """Every vendor this codebase can key, and what each one serves.

    What the add-key form leads with: picking a provider first and reading its
    components off this, rather than asking "which component" before the
    vendor is even named — which made adding one Sarvam key feel like adding
    three, because nothing on the form said Sarvam already covers all of them.

    "realtime" among a vendor's components does not mean a *separate* key is
    needed — a speech-to-speech vendor authenticates with its ordinary
    sibling's key. ``realtime_provider`` names which realtime provider that
    key unlocks, so the screen can ask model discovery about the right vendor
    rather than the one whose key is actually stored.
    """
    return {
        "providers": [
            {
                "provider": provider,
                "components": sorted(components),
                "realtime_provider": realtime_provider_for(provider),
            }
            for provider, components in sorted(known_providers().items())
        ]
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


@router.post("/recheck")
async def recheck_provider_keys() -> dict[str, Any]:
    """Ask every vendor, now, whether the key we hold is still good.

    The scheduled check runs hourly, which is the right cadence for catching a
    key that was revoked while nobody was looking and the wrong one for the
    minute after an operator has replaced it. Without this they would have to
    wait out the hour, or place a test call and read the logs, to find out
    whether the fix worked.
    """
    async with db_client.async_session() as session:
        results = await credential_validation.validate_stored_credentials(session)
        stored = await creds.list_credentials(session)
    return {
        "credentials": [_view(c) for c in stored],
        "checked": len(results),
        # validate_stored_credentials returns CredentialCheck records, not
        # tuples. It used to return tuples, and this counted them by unpacking
        # three-wide; the dataclass that replaced them is not iterable, so
        # every call to this endpoint died on a TypeError before it reached
        # the response — the recheck button appeared to do nothing but 500.
        "rejected": sum(1 for c in results if c.ok is False),
        # Vendors we could not reach, and vendors we have no probe for. Neither
        # is a failure and neither changes a stored verdict — but an operator
        # who just replaced a key deserves to know we did not actually confirm
        # it rather than being shown a silent pass.
        "unverified": sum(1 for c in results if c.ok is None),
    }


@router.get("/balances")
async def read_provider_balances() -> dict[str, Any]:
    """How much is left in the accounts our keys draw on.

    Separate from ``/recheck`` because it answers a separate question. A key
    can be perfectly valid and its account empty — that is not an edge case,
    it is the normal way running out of credit looks, and every existing check
    on this screen passes right through it.

    Not on a schedule of its own here: this is the operator asking now. The
    hourly sweep lives in ``tasks/provider_balances.py``.
    """
    async with db_client.async_session() as session:
        balances = await provider_balance.read_all(session)
    return {
        "balances": [_balance_view(b) for b in balances],
        # What an operator should act on today. Counted here rather than in the
        # client so the "N accounts need attention" badge and the hourly alert
        # cannot disagree about what counts.
        "needs_attention": sum(1 for b in balances if b.needs_attention),
    }


def _balance_view(balance: provider_balance.ProviderBalance) -> dict[str, Any]:
    return {
        "provider": balance.provider,
        "status": balance.status,
        "kind": balance.kind,
        "amount": balance.amount,
        "currency": balance.currency,
        "used": balance.used,
        "limit": balance.limit,
        "remaining": balance.remaining,
        "renews_at": balance.renews_at,
        "detail": balance.detail,
    }


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
        # vendor issues one key for all of them. ``resolve_api_key`` itself
        # knows which vault provider a realtime name falls back to (Grok
        # Realtime to xAI, not a stripped "grok" that does not exist), so the
        # provider name is passed through unchanged rather than trimmed here.
        if api_key is None and component in ("realtime", "embeddings"):
            api_key = await creds.resolve_api_key(
                session, component="llm", provider=provider
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
