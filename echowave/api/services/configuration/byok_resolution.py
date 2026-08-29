"""Fill a BYOK section's key from the account's vault.

The exact mirror of ``managed_resolution``, for the other key source. Between
them, every section of an effective configuration arrives at the pipeline with
a real provider and a real key, and the pipeline never learns which of the two
answered:

* ``managed_resolution`` — the section says ``decibyl``. Turn the tier into a
  vendor and authenticate with **our** key.
* ``byok_resolution`` — the section names a vendor and carries no key. Look up
  **the account's** key for that vendor and component.

That symmetry is what makes a mixed stack work. A customer running our Indic
STT alongside their own OpenAI key has one section resolved by each module, and
neither needs to know the other ran.

**Order matters, and it is byok-then-managed.** A managed section has no key
until ``managed_resolution`` supplies one, so running this second would see a
resolved managed section as a keyless BYOK section and go looking in the vault
for a key the customer was never asked for. Running it first means every
section this module sees is one the customer chose a vendor for.

**An inline key always wins.** Configurations written before the vault existed
carry their key in the JSON, and a workflow override may legitimately carry a
one-off key. Looking up the vault only when the section has no key of its own
means neither case changes behaviour on the call after this ships.
"""

from __future__ import annotations

from loguru import logger

from api.db import db_client
from api.enums import CostComponent
from api.services.configuration import organization_credentials
from api.services.configuration.masking import MASK_MARKER

#: Sections that can run on a customer key, and which credential authenticates
#: them. Embeddings deliberately resolve against the LLM credential: a vendor
#: issues one key that serves chat and embeddings alike, so a separate slot
#: would mean pasting the same Google key twice and the pipeline breaking
#: whenever only one of them was rotated. ``managed_resolution`` makes the same
#: choice for the same reason.
BYOK_SECTIONS: tuple[tuple[str, CostComponent], ...] = (
    ("stt", CostComponent.STT),
    ("llm", CostComponent.LLM),
    ("tts", CostComponent.TTS),
    ("realtime", CostComponent.LLM),
    ("embeddings", CostComponent.LLM),
)


#: Ordered backups, which need a key resolved and a key_source stamped exactly
#: like the primary they stand in for. Skipping them would leave a backup with
#: no key -- silence at the moment the primary failed, which is the one moment
#: it exists for -- and unattributed usage if it ever ran.
FALLBACK_SECTIONS: tuple[tuple[str, CostComponent], ...] = (
    ("fallback_stt", CostComponent.STT),
    ("fallback_tts", CostComponent.TTS),
)


def _all_sections(effective):
    """Yield ``(label, component, section)`` for every section needing a key.

    A label rather than an attribute name, because a backup is one entry of a
    list and "fallback_tts" alone would not say which.
    """
    for name, component in BYOK_SECTIONS:
        section = getattr(effective, name, None)
        if section is not None:
            yield name, component, section

    for name, component in FALLBACK_SECTIONS:
        for index, section in enumerate(getattr(effective, name, None) or []):
            yield f"{name}[{index}]", component, section


def _is_managed(section) -> bool:
    return section is not None and section.is_managed


def _is_usable(key) -> bool:
    """A key we could actually authenticate with.

    A masked key is not one. Responses mask stored keys to ``****...cdef``
    before they leave the API, and both save paths refuse to persist a masked
    value — but treating one as usable here would be the worst of both worlds:
    the vault fallback would not fire, and the call would reach the vendor with
    a string of asterisks and fail on *their* authentication error, three layers
    from anything that explains it.

    Cheap insurance rather than a live defect. It costs one substring check on
    a path that already decided to look at the key.
    """
    return isinstance(key, str) and bool(key.strip()) and MASK_MARKER not in key


def _has_own_key(section) -> bool:
    """Whether the section already carries a usable key of its own.

    ``api_key`` is a list on some providers (several keys, rotated round-robin)
    and a string on others, so both shapes are checked rather than assuming the
    one the current provider happens to use.
    """
    api_key = getattr(section, "api_key", None)
    if isinstance(api_key, (list, tuple)):
        return any(_is_usable(k) for k in api_key)
    return _is_usable(api_key)


def _provider_value(section) -> str:
    provider = getattr(section, "provider", None)
    return provider.value if hasattr(provider, "value") else str(provider or "")


async def apply(
    effective,
    *,
    organization_id: int | None,
    allow_managed_fallback: bool = False,
) -> list[str]:
    """Fill every keyless BYOK section from the vault, in place.

    Returns the names of sections still without a key, so a caller that wants
    to refuse the call up front can. Nothing is raised here: one unusable
    section should fail in that provider's own branch with that vendor's error,
    which is far more diagnosable than a generic one raised three layers away
    from the thing that is actually misconfigured.

    ``allow_managed_fallback`` is the account's answer to "what should happen if
    my key is missing", read from its preferences. **False is the default and
    the safe one**: the section is left unresolved and the caller refuses the
    call, so nobody is billed for something they did not choose. True switches
    the section to Decibyl's key for the same vendor and model, and the call is
    billed at the published rate — which is why it is opt-in and why the screen
    that offers it says what it costs.

    Whichever is chosen, the outcome is never silence. Leaving the section
    pointing at a vendor with an empty key connected the call and gave the
    caller nothing to listen to, which is worse than either answer here.

    Mutates rather than copying for the same reason ``managed_resolution`` does
    — the caller owns the object and every consumer reads it by attribute, so a
    second representation would only create a way to use the wrong one.
    """
    # Stamped for every present section, managed or not, before anything else
    # runs -- this is the only point where a section's own ``provider`` value
    # still says who it belongs to. ``managed_resolution`` overwrites it to a
    # real vendor name a moment later, and from then on a managed section and
    # a BYOK one are indistinguishable by inspection. Billing reads this back
    # off ``usage_info`` at the end of the call to decide whether a component
    # was ours to charge for or the account's to have paid the vendor for
    # directly -- see ``api/services/billing/usage.py``.
    for _label, _component, section in _all_sections(effective):
        section.key_source = "managed" if _is_managed(section) else "byok"

    if organization_id is None:
        return []

    candidates = [
        (label, component, section)
        for label, component, section in _all_sections(effective)
        if not _is_managed(section) and not _has_own_key(section)
    ]
    if not candidates:
        return []

    unresolved: list[str] = []
    async with db_client.async_session() as session:
        for name, component, section in candidates:
            provider = _provider_value(section)

            api_key = await organization_credentials.resolve_api_key(
                session,
                organization_id=organization_id,
                component=component,
                provider=provider,
            )
            if not api_key:
                unresolved.append(name)

                if not allow_managed_fallback:
                    # Left unresolved on purpose. The caller checks the returned
                    # list before dialling and refuses the call, which is the
                    # honest outcome: the account asked to run on its own key,
                    # that key is not usable, and running on ours instead would
                    # bill them for a choice they did not make.
                    logger.warning(
                        "{} is set to {} on this account's own key, but no "
                        "active key for {} is stored. This call will be "
                        "refused. Add a key under Provider Keys, set the slot "
                        "to managed, or turn on falling back to Decibyl's keys.",
                        name,
                        provider,
                        provider,
                    )
                    continue

                # Opted in. Fail over to managed rather than leaving the section
                # pointing at a vendor with an empty key.
                #
                # This is the behaviour `POST /provider-keys/active` promises in
                # so many words — "the agents pointing at it fail over to
                # managed rather than authenticating with a key that is being
                # revoked" — and the reason an admin is told pausing is the safe
                # move while rotating at the vendor.
                #
                # `key_source` has to move with it. It was stamped "byok" above,
                # and billing reads it back off `usage_info` to decide whether a
                # component was ours to charge for; leaving it would run the
                # call on our vendor key and then decline to bill it.
                section.use_platform_key = True
                section.key_source = "managed"
                logger.warning(
                    "{} is set to {} on this account's own key, but no active "
                    "key for {} is stored — falling back to the managed key and "
                    "billing this component at the published rate, as this "
                    "account has opted into.",
                    name,
                    provider,
                    provider,
                )
                continue

            section.api_key = api_key
            logger.debug(
                "Resolved {} key for {} from the account vault.", name, provider
            )

    return unresolved


async def missing_keys(effective, *, organization_id: int | None) -> list[str]:
    """Which BYOK sections this account cannot currently run.

    Read-only: unlike :func:`apply` it leaves the configuration alone, so a
    validity check or a pre-flight screen can ask the question without the side
    effect of loading secrets into an object that is about to be discarded.
    """
    if organization_id is None:
        return []

    missing: list[str] = []
    async with db_client.async_session() as session:
        for name, component in BYOK_SECTIONS:
            section = getattr(effective, name, None)
            if section is None or _is_managed(section) or _has_own_key(section):
                continue
            api_key = await organization_credentials.resolve_api_key(
                session,
                organization_id=organization_id,
                component=component,
                provider=_provider_value(section),
            )
            if not api_key:
                missing.append(name)
    return missing
