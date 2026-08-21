"""Every managed tier must resolve to something we can charge for.

This is the guard for a specific, repeated failure. A managed tier is a
promise that we hold the key and bill the customer; if the model it resolves
to has no row in the rate card, the call does not fail — it costs zero, marks
the run uncosted, and inflates reported margin by exactly the amount it failed
to charge. Nothing goes red.

It has happened twice in this codebase's history and both were found by
reading rather than by a test:

* ElevenLabs multilingual v2 fell through to the provider-wide row, which is
  deliberately the cheapest common model, and was resold below cost.
* ``gpt-4.1`` — the OpenAI default in the picker — had no row of its own and
  was priced at roughly a thirteenth of its cost.

Repointing a tier is a one-line change that anybody can make safely *except*
for this, so the check belongs next to the tiers rather than in somebody's
head.
"""

import pytest

from api.enums import CostComponent
from api.services.billing import default_rates
from api.services.configuration import managed_tiers


def _rate_for(provider: str, model: str, component: CostComponent):
    """Resolve a rate the way ``resolve_provider_rate`` does: exact, else provider-wide."""
    rows = [
        r
        for r in default_rates.DEFAULT_RATES
        if r.provider == provider and r.component == component
    ]
    exact = [r for r in rows if r.model == model]
    if exact:
        return exact[0]
    fallback = [r for r in rows if r.model == ""]
    return fallback[0] if fallback else None


#: (component, the CostComponent it bills as). Embeddings are absent
#: deliberately — they are consumed at knowledge-base ingest rather than per
#: call, so there is no per-call line item and nothing to price here.
_BILLED = (
    ("llm", CostComponent.LLM),
    ("stt", CostComponent.STT),
    ("tts", CostComponent.TTS),
)


def _tiers():
    for component, cost_component in _BILLED:
        tiers = {
            "llm": managed_tiers.LLM_TIERS,
            "stt": managed_tiers.STT_TIERS,
            "tts": managed_tiers.TTS_TIERS,
        }[component]
        for tier in tiers:
            yield component, cost_component, tier


@pytest.mark.parametrize(
    "component,cost_component,tier",
    list(_tiers()),
    ids=lambda v: str(v),
)
def test_every_offered_tier_has_a_price(component, cost_component, tier):
    upstream = managed_tiers.resolve(component, tier)
    rate = _rate_for(upstream.provider, upstream.model, cost_component)

    assert rate is not None, (
        f"The managed {component} tier {tier!r} resolves to "
        f"{upstream.provider}/{upstream.model}, which has no rate row. A call "
        f"on it would bill zero and report as uncosted."
    )
    assert rate.usd_per_unit > 0, (
        f"{upstream.provider}/{upstream.model} is priced at zero."
    )


@pytest.mark.parametrize("component,cost_component,tier", list(_tiers()), ids=str)
def test_a_tier_is_priced_by_name_not_by_fallback(component, cost_component, tier):
    """The row should name the model, not lean on the provider-wide fallback.

    A provider-wide row is a safety net for a model nobody priced, and it is
    deliberately set to the cheapest common one so an unpriced model
    under-reports rather than over-reports. A *tier* is a product we sell, and
    leaning on that net means the day the vendor ships another model, the tier
    and the newcomer are priced identically.
    """
    upstream = managed_tiers.resolve(component, tier)
    named = [
        r
        for r in default_rates.DEFAULT_RATES
        if r.provider == upstream.provider
        and r.component == cost_component
        and r.model == upstream.model
    ]
    assert named, (
        f"The managed {component} tier {tier!r} resolves to "
        f"{upstream.provider}/{upstream.model}, which is priced only by the "
        f"provider-wide fallback. Give it a row of its own."
    )


def test_retired_tier_names_still_price(self=None):
    """A stored configuration naming a retired tier must still cost correctly."""
    for tier in ("fast", "zen"):
        upstream = managed_tiers.resolve("llm", tier)
        assert _rate_for(upstream.provider, upstream.model, CostComponent.LLM)
