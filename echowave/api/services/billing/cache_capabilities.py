"""Which model vendors cache prompts, how, and what billing knows they charge.

Data, not prose: one row per vendor the platform can route a model call to,
and one per model the configuration registry lists for it. The report and the
staff screen read this; nothing here changes a request.

Three sources, each named so a row can be checked against it:

* **Mechanism and minimum size** are the vendors' own documented behaviour,
  written down per vendor in :data:`VENDORS`. Where the platform has no
  evidence -- no documentation recorded here and nothing in the pipeline
  reporting a cached count -- the field is ``None`` and is shown as
  ``"unknown"``. A guess here would be read as a fact by the next person
  deciding where to send traffic.
* **Whether our own requests use it** is read from the code paths that exist
  today (``pipecat.service_factory`` turns Anthropic's caching on; the
  builder client marks Anthropic's system block), recorded in
  :data:`VENDORS` as ``platform_requests_cache``.
* **Prices** come from billing's own tables (``default_rates.
  CACHED_INPUT_SHARE`` / ``CACHED_INPUT_SHARE_BY_MODEL`` /
  ``CACHE_WRITE_SHARE``), never from here. Billing's ``cached_input_share``
  treats a vendor it has no row for as charging cached input at 1.0x; this
  matrix does not, because "no row" is not evidence of "no discount" -- it
  says ``unknown``.

A registered vendor with no entry in :data:`VENDORS` still gets a row, every
field unknown, so a vendor added to the registry tomorrow appears here rather
than vanishing (api/AGENTS.md, Silent Absence).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

UNKNOWN = "unknown"

#: How a vendor's cache is reached.
AUTOMATIC_PREFIX = "automatic_prefix"
EXPLICIT_CACHE_CONTROL = "explicit_cache_control"


@dataclass(frozen=True)
class VendorCaching:
    """What one vendor does with a repeated prompt prefix.

    ``supported`` -- the vendor caches prompts at all (``None``: unknown).
    ``mechanism`` -- :data:`AUTOMATIC_PREFIX` (the vendor matches a repeated
    prefix by itself) or :data:`EXPLICIT_CACHE_CONTROL` (the request has to
    mark what to cache); ``None``: unknown.
    ``min_cacheable_tokens`` -- the smallest prefix the vendor will cache,
    as documented; ``None``: unknown or differs by model with no figure here.
    ``min_varies_by_model`` -- the figure is a floor and some models need more.
    ``platform_requests_cache`` -- our own request paths ask for it today
    (always true for an automatic cache, which needs no asking).
    ``reports_cache`` -- the vendor's usage says how much its cache served.
    """

    supported: bool | None = None
    mechanism: str | None = None
    min_cacheable_tokens: int | None = None
    min_varies_by_model: bool = False
    platform_requests_cache: bool | None = None
    reports_cache: bool | None = None


#: Vendor -> caching, keyed by the provider name usage and billing record
#: (``billing.usage.provider_from_processor`` and the builder client).
VENDORS: dict[str, VendorCaching] = {
    # Explicit breakpoints; the pipeline marks the last two user turns
    # (pipecat's Anthropic adapter, enable_prompt_caching in service_factory)
    # and the builder client marks the system block.
    "anthropic": VendorCaching(
        supported=True,
        mechanism=EXPLICIT_CACHE_CONTROL,
        min_cacheable_tokens=1024,
        min_varies_by_model=True,
        platform_requests_cache=True,
        reports_cache=True,
    ),
    # The same Messages body through AWS (services/aws_gateway/claude.py).
    "anthropic_aws": VendorCaching(
        supported=True,
        mechanism=EXPLICIT_CACHE_CONTROL,
        min_cacheable_tokens=1024,
        min_varies_by_model=True,
        platform_requests_cache=True,
        reports_cache=True,
    ),
    # Converse takes explicit cache points; nothing on our pipeline path
    # sets one, so only the Claude-through-AWS door above caches today.
    "aws_bedrock": VendorCaching(
        supported=True,
        mechanism=EXPLICIT_CACHE_CONTROL,
        min_varies_by_model=True,
        platform_requests_cache=False,
        reports_cache=True,
    ),
    "openai": VendorCaching(
        supported=True,
        mechanism=AUTOMATIC_PREFIX,
        min_cacheable_tokens=1024,
        platform_requests_cache=True,
        reports_cache=True,
    ),
    "azure": VendorCaching(
        supported=True,
        mechanism=AUTOMATIC_PREFIX,
        min_cacheable_tokens=1024,
        min_varies_by_model=True,
        platform_requests_cache=True,
        reports_cache=True,
    ),
    # Implicit caching is automatic; explicit cachedContent exists and is not
    # used anywhere here.
    "google": VendorCaching(
        supported=True,
        mechanism=AUTOMATIC_PREFIX,
        min_varies_by_model=True,
        platform_requests_cache=True,
        reports_cache=True,
    ),
    "google_vertex": VendorCaching(
        supported=True,
        mechanism=AUTOMATIC_PREFIX,
        min_varies_by_model=True,
        platform_requests_cache=True,
        reports_cache=True,
    ),
    "deepseek": VendorCaching(
        supported=True,
        mechanism=AUTOMATIC_PREFIX,
        platform_requests_cache=True,
        reports_cache=True,
    ),
    # Speech-to-speech services whose pipecat adapters report a cached count.
    "openai_realtime": VendorCaching(
        supported=True,
        mechanism=AUTOMATIC_PREFIX,
        platform_requests_cache=True,
        reports_cache=True,
    ),
    "google_realtime": VendorCaching(
        supported=True,
        mechanism=AUTOMATIC_PREFIX,
        platform_requests_cache=True,
        reports_cache=True,
    ),
    "google_vertex_realtime": VendorCaching(
        supported=True,
        mechanism=AUTOMATIC_PREFIX,
        platform_requests_cache=True,
        reports_cache=True,
    ),
}

#: Provider names usage is recorded under that are not registry entries:
#: Claude through AWS is a door, not a configuration.
EXTRA_PROVIDERS: tuple[str, ...] = ("anthropic_aws",)


def _registered() -> dict[str, list[str]]:
    """Every LLM and realtime vendor the registry can route to, with the
    models it lists for each (its ``examples``)."""
    from api.services.configuration.registry import REGISTRY, ServiceType

    out: dict[str, list[str]] = {}
    for service_type in (ServiceType.LLM, ServiceType.REALTIME):
        for provider, cls in REGISTRY[service_type].items():
            name = getattr(provider, "value", provider)
            field = getattr(cls, "model_fields", {}).get("model")
            extra = (getattr(field, "json_schema_extra", None) or {}) if field else {}
            examples = extra.get("examples") if isinstance(extra, dict) else None
            models = out.setdefault(str(name), [])
            for model in examples or []:
                if isinstance(model, str) and model not in models:
                    models.append(model)
    for name in EXTRA_PROVIDERS:
        out.setdefault(name, [])
    return out


def vendor(provider: str) -> VendorCaching:
    """What is known about ``provider``; every field unknown if nothing is."""
    return VENDORS.get(provider or "", VendorCaching())


def read_multiplier(provider: str, model: str = "") -> float | None:
    """Cached input as a multiple of input, from billing; None if billing
    has no figure for this vendor (or this model)."""
    from api.services.billing import default_rates

    by_model = default_rates.CACHED_INPUT_SHARE_BY_MODEL.get((provider, model))
    if by_model is not None:
        return by_model
    return default_rates.CACHED_INPUT_SHARE.get(provider)


def write_multiplier(provider: str) -> float | None:
    """A cache write as a multiple of input, from billing; None if billing
    has no figure (which, for an automatic cache, usually means the write is
    not charged separately -- but that is said by the mechanism, not here)."""
    from api.services.billing import default_rates

    return default_rates.CACHE_WRITE_SHARE.get(provider)


def min_cacheable_tokens(provider: str) -> int | None:
    return vendor(provider).min_cacheable_tokens


def _shown(value: Any) -> Any:
    return UNKNOWN if value is None else value


def row(provider: str, model: str = "") -> dict[str, Any]:
    """One matrix row, unknowns spelled out as ``"unknown"``."""
    v = vendor(provider)
    return {
        "provider": provider,
        "model": model,
        "known_vendor": provider in VENDORS,
        "supported": _shown(v.supported),
        "mechanism": _shown(v.mechanism),
        "min_cacheable_tokens": _shown(v.min_cacheable_tokens),
        "min_varies_by_model": v.min_varies_by_model,
        "platform_requests_cache": _shown(v.platform_requests_cache),
        "reports_cache": _shown(v.reports_cache),
        "cache_read_multiplier": _shown(read_multiplier(provider, model)),
        "cache_write_multiplier": _shown(write_multiplier(provider)),
    }


def matrix() -> list[dict[str, Any]]:
    """Every vendor (a row with model ``""``) and every model the registry
    lists for it, in a stable order."""
    rows: list[dict[str, Any]] = []
    for provider, models in sorted(_registered().items()):
        rows.append(row(provider, ""))
        rows.extend(row(provider, model) for model in models)
    return rows


__all__ = [
    "AUTOMATIC_PREFIX",
    "EXPLICIT_CACHE_CONTROL",
    "UNKNOWN",
    "VENDORS",
    "VendorCaching",
    "matrix",
    "min_cacheable_tokens",
    "read_multiplier",
    "row",
    "vendor",
    "write_multiplier",
]
