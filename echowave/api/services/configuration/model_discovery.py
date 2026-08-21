"""What models a vendor actually serves, asked with a key we hold.

An operator installing a provider key should not then have to go and read that
vendor's documentation to find out what they just bought. Nor should a customer
bringing their own key have to type a model id from memory and discover the typo
on a live call.

So: given a key, ask the vendor. Three properties matter more than the list.

**It never raises, and it always answers.** A vendor that is down, rate-limiting,
or has no list endpoint at all falls back to the models this codebase already
knows about — the ``examples`` on the registry's own configuration classes. The
answer says which of the two it is, because "these are the models OpenAI told us
about" and "these are the ones we have heard of" are different claims and a
screen that presents them identically is lying by omission.

**It does not classify.** OpenAI's ``/v1/models`` returns embeddings, image
models and transcription models in one list with no component field. Guessing
which are chat models from their ids is a heuristic that goes stale every
release, and being wrong means a customer picks a model that cannot serve the
slot. Instead every model is returned and the ones this codebase already lists
for that component are flagged ``suggested`` — the operator selects, which is
what they were going to do anyway.

**A key is never logged, returned, or stored by this module.** It arrives, it is
put in one header, and it goes out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from loguru import logger

#: Short. This runs inside a request an operator is watching, and a vendor that
#: has not answered in this long should be reported as unreachable rather than
#: hold the screen open.
TIMEOUT_SECONDS = 12.0

#: Vendors whose model list is an OpenAI-shaped ``GET {base}/models`` with a
#: bearer token. Several vendors are deliberately OpenAI-compatible, and each
#: one added here is a base URL rather than a new code path.
_OPENAI_COMPATIBLE: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "openai_realtime": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "xai": "https://api.x.ai/v1",
    "deepseek": "https://api.deepseek.com/v1",
}

#: Google AI Studio. A different shape and a key in the query string rather than
#: a header, which is why it is not in the table above.
_GOOGLE_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"


@dataclass(frozen=True)
class DiscoveredModel:
    id: str
    #: True when this codebase already names it for the component asked about.
    #: Not a judgement about the model — only about whether we have integrated
    #: and tested it.
    suggested: bool = False
    #: True when it is already in the managed catalogue. Filled in by the
    #: caller, which is the thing that knows what we sell.
    offered: bool = False


@dataclass(frozen=True)
class Discovery:
    """The answer, and how much it is worth."""

    provider: str
    component: str
    models: tuple[DiscoveredModel, ...] = ()
    #: ``vendor`` — the vendor listed these. ``catalogue`` — the vendor could
    #: not be asked and these are the ones we know of.
    source: str = "catalogue"
    #: Set when the vendor was asked and could not answer. Shown, because an
    #: operator seeing a short list needs to know whether it is short because
    #: the vendor is small or because the request failed.
    note: str | None = None

    @property
    def from_vendor(self) -> bool:
        return self.source == "vendor"


def known_models(component: str, provider: str) -> tuple[str, ...]:
    """The models this codebase already names for a slot.

    Read out of the registry's configuration classes rather than a list kept
    here, so a model added to the platform is discoverable the day it lands.
    """
    from api.services.configuration.registry import REGISTRY, ServiceType

    service_type = {
        "llm": ServiceType.LLM,
        "stt": ServiceType.STT,
        "tts": ServiceType.TTS,
        "realtime": ServiceType.REALTIME,
        "embeddings": ServiceType.EMBEDDINGS,
    }.get((component or "").strip().lower())
    if service_type is None:
        return ()

    wanted = (provider or "").strip().lower()
    for registered, config_class in REGISTRY.get(service_type, {}).items():
        if registered.value != wanted:
            continue
        model_field = config_class.model_fields.get("model")
        if model_field is None:
            return ()
        extra = model_field.json_schema_extra or {}
        if not isinstance(extra, dict):
            return ()
        return tuple(str(m) for m in (extra.get("examples") or []))
    return ()


def allows_custom_model(component: str, provider: str) -> bool:
    """Whether the vendor takes model strings we do not enumerate.

    Several do, and they ship new models faster than we cut releases. Where this
    is true a screen should let one be typed; where it is false, typing one
    produces a call that fails at session open.
    """
    from api.services.configuration.registry import REGISTRY, ServiceType

    service_type = {
        "llm": ServiceType.LLM,
        "stt": ServiceType.STT,
        "tts": ServiceType.TTS,
        "realtime": ServiceType.REALTIME,
        "embeddings": ServiceType.EMBEDDINGS,
    }.get((component or "").strip().lower())
    if service_type is None:
        return False

    wanted = (provider or "").strip().lower()
    for registered, config_class in REGISTRY.get(service_type, {}).items():
        if registered.value != wanted:
            continue
        model_field = config_class.model_fields.get("model")
        extra = (model_field.json_schema_extra or {}) if model_field else {}
        if isinstance(extra, dict):
            return bool(extra.get("allow_custom_input"))
    return False


async def _openai_compatible(
    *, base_url: str, api_key: str, client: httpx.AsyncClient
) -> list[str]:
    response = await client.get(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    response.raise_for_status()
    payload = response.json()
    return [
        str(entry.get("id"))
        for entry in (payload.get("data") or [])
        if isinstance(entry, dict) and entry.get("id")
    ]


async def _google(*, api_key: str, client: httpx.AsyncClient) -> list[str]:
    response = await client.get(_GOOGLE_MODELS_URL, params={"key": api_key})
    response.raise_for_status()
    payload = response.json()
    out: list[str] = []
    for entry in payload.get("models") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "")
        # Google returns "models/gemini-2.5-flash"; the factory sends the bare
        # id, so strip the prefix here rather than in three call sites.
        out.append(name.split("/", 1)[1] if name.startswith("models/") else name)
    return [name for name in out if name]


async def _ask_vendor(
    *, provider: str, api_key: str, client: httpx.AsyncClient | None
) -> list[str] | None:
    """The vendor's own list, or ``None`` when it cannot be obtained.

    ``None`` covers both "no list endpoint for this vendor" and "the request
    failed" — the caller distinguishes them by whether a note came back.
    """
    provider = (provider or "").strip().lower()
    base_url = _OPENAI_COMPATIBLE.get(provider)
    if base_url is None and provider not in ("google", "google_realtime"):
        return None

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        if base_url is not None:
            return await _openai_compatible(
                base_url=base_url, api_key=api_key, client=client
            )
        return await _google(api_key=api_key, client=client)
    finally:
        if owns_client:
            await client.aclose()


async def discover(
    *,
    component: str,
    provider: str,
    api_key: str | None,
    client: httpx.AsyncClient | None = None,
) -> Discovery:
    """What ``provider`` serves, asked with ``api_key`` where we can ask.

    Falls back to the models this codebase knows about, always. A discovery
    with an empty list is a failure the caller has to handle; a discovery that
    fell back is one they can act on.
    """
    component = (component or "").strip().lower()
    provider = (provider or "").strip().lower()
    known = known_models(component, provider)

    listed: list[str] | None = None
    note: str | None = None

    if api_key:
        try:
            listed = await _ask_vendor(
                provider=provider, api_key=api_key, client=client
            )
        except httpx.HTTPStatusError as exc:
            note = (
                f"{provider} answered {exc.response.status_code} when asked for "
                "its models. Showing the models we already know about."
            )
            logger.warning("Model discovery for {} failed: {}", provider, exc)
        except Exception as exc:  # noqa: BLE001 — every failure is an outcome
            note = (
                f"Could not reach {provider} to list its models. Showing the "
                "models we already know about."
            )
            logger.warning("Model discovery for {} failed: {}", provider, exc)
    else:
        note = "No key stored for this provider, so its models were not listed."

    if not listed:
        return Discovery(
            provider=provider,
            component=component,
            models=tuple(DiscoveredModel(id=model, suggested=True) for model in known),
            source="catalogue",
            note=note,
        )

    suggested = set(known)
    # The vendor's order is arbitrary; ours puts the models we have integrated
    # first, then everything else alphabetically. An operator ticking boxes
    # should not have to hunt for the one they came for.
    ranked = sorted(listed, key=lambda m: (m not in suggested, m))
    return Discovery(
        provider=provider,
        component=component,
        models=tuple(
            DiscoveredModel(id=model, suggested=model in suggested) for model in ranked
        ),
        source="vendor",
        note=None,
    )
