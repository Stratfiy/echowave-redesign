"""Every ElevenLabs model we offer carries its own rate row.

The provider-wide fallback is Flash's $0.05. Multilingual v2 costs twice that,
and before it had a row of its own an account using it was billed at half what
it cost us -- recorded in default_rates as the one case where the card was
knowingly under the vendor.

So the rule is not "a rate exists" but "a rate exists for this model". A model
added to the picker without one does not fail, and that is the problem: it
works, bills at the cheapest model's rate, and reports margin nobody earned.
"""

from __future__ import annotations

import pytest

from api.enums import CostComponent
from api.services.billing.default_rates import DEFAULT_RATES
from api.services.configuration.registry import ELEVENLABS_TTS_MODELS

_TTS_RATES = {
    r.model: r
    for r in DEFAULT_RATES
    if r.provider == "elevenlabs" and r.component == CostComponent.TTS
}


@pytest.mark.parametrize("model", ELEVENLABS_TTS_MODELS)
def test_every_offered_model_has_its_own_rate(model):
    assert model in _TTS_RATES, (
        f"{model} is in the picker with no rate row of its own, so it bills at "
        "the provider-wide rate. That is Flash's price, and anything dearer "
        "than Flash is then billed under cost."
    )


@pytest.mark.parametrize("model", ELEVENLABS_TTS_MODELS)
def test_no_offered_model_is_cheaper_than_the_fallback(model):
    """The fallback should under-report, never over-report: a surprise on an
    invoice ought to be a pleasant one."""
    fallback = _TTS_RATES[""]
    assert fallback.usd_per_unit <= _TTS_RATES[model].usd_per_unit


def test_the_deprecated_turbo_models_are_not_offered():
    """ElevenLabs' own documentation replaces both with Flash, which is faster
    on average and covers the same languages."""
    assert not {"eleven_turbo_v2", "eleven_turbo_v2_5"} & set(ELEVENLABS_TTS_MODELS)
